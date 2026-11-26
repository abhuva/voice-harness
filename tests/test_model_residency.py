from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from voice_harness.admission import Admission
from voice_harness.contracts import CallInput, VoiceError
from voice_harness.events import EventBus
from voice_harness.harness import Harness
from voice_harness.presets import Playback
from voice_harness.residency import ModelKey, ModelResidency
from voice_harness.state import State
from voice_harness.synthesis import SynthesisFactory
from voice_harness.work_types import Lineage, ResourceProfile
from voice_harness.worker import CallOperation


class Model:
    def __init__(self):
        self.ready = False
        self.loads = 0
        self.closed = 0
        self.process_id = None

    def prepare(self, stop):
        self.loads += 1
        self.ready = True

    def close(self):
        self.closed += 1
        self.ready = False


class ModelResidencyTests(unittest.TestCase):
    def setUp(self):
        self.events = EventBus()
        self.models = ModelResidency(self.events, measure=lambda _: 300)
        self.addCleanup(self.models.evict_idle)
        self.stop = Event()
        self.stage = Mock()
        self.scope = Lineage("call", "job")
        self.stt = ModelKey("stt", ("multilingual", 4))
        self.tts = ModelKey("tts", ("alba",))

    def test_reuse_releases_the_claim_and_keeps_the_model(self):
        create = Mock(side_effect=Model)
        with self.events.subscribe() as notices:
            with self.models.borrow(self.stt, create, self.stop, self.stage, self.scope) as first:
                self.assertFalse(self.models.evict_idle())
                with self.assertRaisesRegex(VoiceError, "already in use"):
                    with self.models.borrow(self.stt, create, self.stop, self.stage, self.scope):
                        self.fail("a second claim was allowed")
            with self.models.borrow(self.stt, create, self.stop, self.stage, self.scope) as second:
                self.assertIs(first, second)
                self.assertEqual(second.closed, 0)
            self.assertTrue(self.models.evict_idle())
            emitted = [notices.get_nowait() for _ in range(4)]
        self.assertEqual([event.kind for event in emitted],
                         ["model.loading", "model.ready", "model.reused", "model.unloaded"])
        self.assertEqual(len({event.model.id for event in emitted}), 1)
        self.assertIsNone(emitted[-1].scope)
        self.assertEqual((first.loads, first.closed), (1, 1))
        create.assert_called_once()

    def test_recognition_expiry_uses_last_utterance_and_cannot_evict_a_claimed_model(self):
        pending = []

        def create_timer(delay, expire):
            timer = Mock()
            pending.append((delay, expire, timer))
            return timer

        self.models.measure = Mock(side_effect=[100, 800, 800, 110])
        create = Mock(side_effect=Model)
        with (patch("voice_harness.residency.Timer", side_effect=create_timer),
              patch("voice_harness.residency.monotonic", return_value=50)):
            with self.models.borrow(self.stt, create, self.stop, self.stage, self.scope,
                                    expires_at=lambda: 70) as first:
                self.assertFalse(pending)
            self.assertEqual(pending[0][0], 20)
            with self.models.borrow(self.stt, create, self.stop, self.stage, self.scope,
                                    expires_at=lambda: 85) as second:
                self.assertIs(first, second)
                pending[0][2].cancel.assert_called_once()
                pending[0][1]()
                self.assertEqual(first.closed, 0)
            self.assertEqual(pending[1][0], 35)
            pending[1][1]()
            self.assertEqual(first.closed, 1)
            self.assertEqual(self.models.snapshot(), ())
            self.assertEqual(self.models.recognition_relief(), (0, 690))

    def test_failed_or_cancelled_model_is_replaced(self):
        class Broken(Model):
            def prepare(self, stop):
                raise VoiceError("fixture failed")

        class Cancelled(Model):
            def prepare(self, stop):
                super().prepare(stop)
                stop.set()

        broken, cancelled = Broken(), Cancelled()
        with self.events.subscribe() as notices:
            with self.assertRaisesRegex(VoiceError, "fixture failed"):
                with self.models.borrow(self.stt, lambda: broken, self.stop, self.stage, self.scope):
                    pass
            with self.models.borrow(self.stt, lambda: cancelled, self.stop, self.stage, self.scope) as held:
                self.assertIsNone(held)
            self.stop.clear()
            with self.models.borrow(self.stt, Model, self.stop, self.stage, self.scope) as warm:
                self.assertTrue(warm.ready)
                warm.ready = False  # A resident worker died while leased.
            tags = []
            while not notices.empty():
                tags.append(notices.get_nowait().kind)
        self.assertEqual((broken.closed, cancelled.closed, warm.closed), (1, 1, 1))
        self.assertEqual(tags.count("model.failed"), 2)
        self.assertEqual(tags.count("model.ready"), 1)

    def test_residency_change_cancels_or_starts_idle_expiry(self):
        first, second = Mock(), Mock()
        with patch("voice_harness.residency.Timer", side_effect=[first, second]) as timer:
            with self.models.borrow(self.stt, Model, self.stop, self.stage, self.scope,
                                    expires_at=lambda: 42) as model:
                pass
            first_expiry = timer.call_args.args[1]
            self.models.set_recognition_idle(60)
            timer.assert_called_once()
            self.models.set_recognition_idle(None)
            first.cancel.assert_called_once()
            first_expiry()
            self.assertEqual(model.closed, 0)
            self.models.set_recognition_idle(60)
            self.assertEqual(timer.call_count, 2)
            timer.call_args.args[1]()
            self.assertEqual(model.closed, 1)

    def test_warm_memory_is_credited_and_other_models_can_be_evicted(self):
        with TemporaryDirectory() as directory:
            available = 900
            harness = Harness(State(Path(directory)), admission=Admission(lambda: available))
            harness.models = self.models
            self.models.measure = Mock(side_effect=[100, 700])
            model = Model()
            with self.models.borrow(self.stt, lambda: model, self.stop, self.stage, self.scope):
                pass
            profile = ResourceProfile(750, 50, 1)
            available = 250
            job = harness.submit("Call", profile=profile, models=(self.stt,))
            self.assertEqual(harness.execute(job, lambda stop: "Warm call"), "Warm call")
            self.assertEqual(model.closed, 0)

            def dispose():
                nonlocal available
                Model.close(model)
                available = 900

            model.close = dispose
            job = harness.submit("Voice cloning", profile=profile)
            self.assertEqual(harness.execute(job, lambda stop: "Clone"), "Clone")
            self.assertEqual(model.closed, 1)

    def test_call_operation_reuses_models_and_switches_only_the_selected_capability(self):
        with TemporaryDirectory() as directory:
            harness = Harness(State(Path(directory)), admission=Admission(lambda: 1024 ** 3))
            self.addCleanup(harness.models.evict_idle)
            settings = SimpleNamespace(dictate=Path(directory) / "dictate", synthesis=SynthesisFactory(Path(directory) / "pocket"),
                                       credentials=lambda: ("fixture", "kin"))
            call_input = CallInput()
            playback = Playback("18m", "alba", 0.3)

            class STT(Model):
                def __init__(self, *_):
                    super().__init__()

                def select_model(self, model, threads):
                    self.selection = model, threads

            class TTS(Model):
                def __init__(self, *_, **__):
                    super().__init__()

            calls = []

            def run(microphone, recognize, speaker, provider, state, options, stop, stage, publish, ready, **kwargs):
                ready()
                self.assertFalse(any(key.kind == "stt" and mode == "In use"
                                     for key, mode, _ in harness.models.snapshot()))
                with recognize("spoken-turn", stop, stage) as recognizer:
                    calls.append((recognizer, speaker.synthesis, speaker.sink.settings.speed, speaker.sink))
                stop.set()

            worker = Mock(harness=harness)
            with (patch("voice_harness.worker.DictateRecognizer", STT),
                  patch("voice_harness.synthesis.PocketSynthesizer", TTS),
                  patch("voice_harness.worker.run_call", side_effect=run),
                  patch("voice_harness.worker.KindroidClient")):
                for tts in (playback, Playback("18m", "alba", 0.3, speed=1.1),
                            Playback("7m", "alba", 0.3)):
                    worker.job = harness.submit("Call")
                    operation = CallOperation(settings, call_input, tts)
                    self.assertEqual(harness.execute(worker.job, lambda stop: operation(worker, stop)),
                                     "Call stopped.")
                    self.assertIsNone(operation.speaker)

            recognizer, tts, _, sink = calls[0]
            self.assertIs(recognizer, calls[2][0])
            self.assertIs(tts, calls[1][1])
            self.assertIsNot(tts, calls[2][1])
            self.assertEqual(calls[1][2], 1.1)
            self.assertIsNot(sink, calls[1][3])
            self.assertEqual((recognizer.loads, recognizer.closed, tts.closed), (1, 0, 1))
            harness.models.evict_idle()
            self.assertEqual((recognizer.closed, calls[2][1].closed), (1, 1))

    def test_resident_call_loads_before_listening_and_reuses_recognizer_after_stop(self):
        with TemporaryDirectory() as directory:
            harness = Harness(State(Path(directory)), admission=Admission(lambda: 1024 ** 3))
            self.addCleanup(harness.models.evict_idle)
            settings = SimpleNamespace(dictate=Path(directory) / "dictate", credentials=lambda: ("fixture", "kin"))
            worker = Mock(harness=harness)
            recognizer = Model()
            recognizer.select_model = Mock()

            def run(_mic, recognize, _speaker, _provider, _state, _options, stop, stage, _publish, ready, **_):
                self.assertEqual(harness.models.snapshot()[0][1], "In use")
                ready()
                for turn in ("first", "second"):
                    with recognize(turn, stop, stage) as model:
                        self.assertIs(model, recognizer)
                stop.set()

            with (patch("voice_harness.worker.DictateRecognizer", return_value=recognizer) as create,
                  patch("voice_harness.worker.run_call", side_effect=run),
                  patch("voice_harness.worker.KindroidClient"),
                  patch("voice_harness.residency.Timer") as timer):
                for _ in range(2):
                    worker.job = harness.submit("Call")
                    operation = CallOperation(settings, CallInput(stt_on_demand=False),
                                              Playback("18m", "alba", 0.3, speed=0))
                    self.assertEqual(harness.execute(worker.job, lambda stop: operation(worker, stop)), "Call stopped.")
                    self.assertEqual(harness.models.snapshot()[0][1], "Warm")
                timer.assert_not_called()
                create.assert_called_once()
                self.assertEqual((recognizer.loads, recognizer.closed), (1, 0))

    def test_call_switches_between_fluid_backends_and_closes_the_old_worker(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            harness = Harness(State(root), admission=Admission(lambda: 2 ** 30))
            self.addCleanup(harness.models.evict_idle)
            settings = SimpleNamespace(dictate=root / "dictate", fluid_vulkan=root / "vulkan",
                                       credentials=lambda: ("fixture", "kin"))
            worker = Mock(harness=harness)
            cpu, gpu = Model(), Model()
            cpu.select_model = Mock()
            selected = []

            def run(_mic, recognize, _speaker, _provider, _state, _options, stop, stage, _publish, ready, **_):
                ready()
                with recognize("turn", stop, stage) as model:
                    selected.append(model)

            with (patch("voice_harness.worker.DictateRecognizer", return_value=cpu),
                  patch("voice_harness.worker.FluidVulkan", return_value=gpu),
                  patch("voice_harness.worker.run_call", side_effect=run),
                  patch("voice_harness.worker.KindroidClient")):
                for backend, dynamic in (("cpu", False), ("vulkan", False), ("cpu", True)):
                    options = CallInput(model="fluid-speech-pro", stt_backend=backend, stt_on_demand=dynamic)
                    worker.job = harness.submit("Call", recognition_on_demand=dynamic)
                    operation = CallOperation(settings, options, Playback("18m", "alba", 0.3, speed=0))
                    self.assertEqual(harness.execute(worker.job, lambda stop: operation(worker, stop)), "Call ended.")
                    self.assertEqual(harness.models.snapshot()[0][0].configuration[-1], backend)
            self.assertEqual(selected, [cpu, gpu, cpu])
            self.assertEqual((cpu.loads, cpu.closed, gpu.loads, gpu.closed), (2, 1, 1, 1))
