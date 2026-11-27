"""Read local tailnet presence and overlay connected Voice Harness endpoints."""

import json
import os
from pathlib import Path
import shutil
from socket import gethostname

from PyQt6.QtCore import QProcess, QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class NodesPanel(QWidget):
    def __init__(self, gateway, host_capabilities, show_routing):
        super().__init__()
        self.gateway = gateway
        self.host_capabilities = host_capabilities
        self.nodes = []
        self.tailnet_available = False
        self.rows = None
        self.cli = shutil.which("tailscale")
        if self.cli is None and os.name == "nt":
            installed = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale/tailscale.exe"
            if installed.is_file():
                self.cli = str(installed)

        layout = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Device", "Tailscale", "Voice Harness capabilities"))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.open_playback = QPushButton("Open playback receiver")
        self.open_playback.setToolTip("Select the Voice Harness host in the device list.")
        self.open_playback.setEnabled(False)
        self.open_playback.clicked.connect(self.open_receiver)
        self.table.currentItemChanged.connect(self.update_receiver_button)
        layout.addWidget(self.open_playback)
        if gateway is not None:
            routing = QPushButton("Open phone routing")
            routing.clicked.connect(show_routing)
            layout.addWidget(routing)

        self.process = QProcess(self)
        self.process.finished.connect(self.read_status)
        self.process.errorOccurred.connect(self.process_error)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.process.kill)
        self.poll = QTimer(self, interval=8000)
        self.poll.timeout.connect(self.refresh)
        self.poll.start()
        self.endpoints = QTimer(self, interval=1000)
        self.endpoints.timeout.connect(self.render)
        self.endpoints.start()
        self.refresh()

    def refresh(self):
        if self.cli and self.process.state() == QProcess.ProcessState.NotRunning:
            self.process.start(self.cli, ["status", "--json"], mode=QProcess.OpenModeFlag.ReadOnly)
            self.deadline.start(4000)
        self.render()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.deadline.stop()
            self.tailnet_available = False
            self.render()

    def read_status(self, code, status):
        self.deadline.stop()
        try:
            if code or status != QProcess.ExitStatus.NormalExit:
                raise ValueError
            data = json.loads(bytes(self.process.readAllStandardOutput()))
            if data["BackendState"] != "Running":
                raise ValueError
            self.nodes = [(True, data["Self"])] + [(False, peer) for peer in (data.get("Peer") or {}).values()]
            self.tailnet_available = True
        except (KeyError, TypeError, ValueError, AttributeError):
            self.tailnet_available = False
        self.render()

    def render(self):
        self.notice.setText("Tailscale presence and connected audio endpoints." if self.tailnet_available
                            else "Tailscale status unavailable. Connected Voice Harness endpoints are shown below.")
        receivers = self.gateway.receivers() if self.gateway else ()
        phone = self.gateway.active if self.gateway else None
        if phone is not None and phone.closed.is_set():
            phone = None
        matches = set()
        rows = []
        routes = []
        phone_matched = False
        host = self.host_capabilities()
        for local, node in self.nodes or [(True, {"HostName": gethostname()})]:
            addresses = node.get("TailscaleIPs") or []
            capabilities = [host] if local else []
            if phone is not None and phone.address in addresses:
                capabilities.append("Phone microphone and playback")
                phone_matched = True
            for receiver in receivers:
                if receiver.address in addresses:
                    capabilities.append(f"Playback ready ({receiver.name})")
                    matches.add(receiver.ident)
            presence = ("Online" if node.get("Online") else "Offline") if self.tailnet_available else "Unknown"
            name = node.get("HostName") or node.get("DNSName") or "Unknown device"
            rows.append((name + (" (this PC)" if local else ""), presence,
                         " · ".join(capabilities) or "No Voice Harness endpoint connected"))
            hostname = (node.get("DNSName") or "").rstrip(".")
            receiver_url = None
            if local and self.gateway is not None:
                receiver_url = f"{self.gateway.origin}/listen"
            elif presence == "Online" and hostname.endswith(".ts.net"):
                url = QUrl()
                url.setScheme("https")
                url.setHost(hostname)
                url.setPath("/listen")
                if url.isValid():
                    receiver_url = url.toString()
            routes.append((hostname, receiver_url))
        if phone is not None and not phone_matched:
            rows.append(("Phone browser", "Unmatched", "Phone microphone and playback"))
            routes.append(("", None))
        for receiver in receivers:
            if receiver.ident not in matches:
                rows.append((receiver.name, "Unmatched", "Playback ready"))
                routes.append(("", None))
        snapshot = list(zip(rows, routes))
        if snapshot == self.rows:
            return
        selected = self.table.currentItem()
        selected_host = selected.data(Qt.ItemDataRole.UserRole) if selected is not None else None
        self.rows = snapshot
        self.table.setRowCount(len(rows))
        self.table.clearSelection()
        self.table.setCurrentItem(None)
        for index, (row, (hostname, receiver_url)) in enumerate(snapshot):
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, hostname)
                item.setData(Qt.ItemDataRole.UserRole + 1, receiver_url)
                self.table.setItem(index, column, item)
            if hostname and hostname == selected_host:
                self.table.selectRow(index)
        self.table.resizeRowsToContents()
        self.update_receiver_button()

    def receiver_url(self):
        item = self.table.currentItem()
        return item.data(Qt.ItemDataRole.UserRole + 1) if item is not None else None

    def update_receiver_button(self):
        self.open_playback.setEnabled(bool(self.receiver_url()))

    def open_receiver(self):
        url = self.receiver_url()
        if url and not QDesktopServices.openUrl(QUrl(url)):
            self.notice.setText("Could not open the playback receiver in your browser.")

    def close_polling(self):
        self.poll.stop()
        self.endpoints.stop()
        self.deadline.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(500)
