import sys
import os
import subprocess
from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtWidgets import (
    QMessageBox, QProgressDialog, QApplication, QDialog,
    QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)
from qgis.PyQt.QtCore import Qt

# ── Enum compatibility: Qgis.MessageLevel (QGIS 4/Qt6) vs Qgis.* (QGIS 3/Qt5)
try:
    _ml = Qgis.MessageLevel
    class MsgLevel:
        Info     = _ml.Info
        Warning  = _ml.Warning
        Critical = _ml.Critical
        Success  = _ml.Success
except AttributeError:
    class MsgLevel:
        Info     = Qgis.Info
        Warning  = Qgis.Warning
        Critical = Qgis.Critical
        Success  = Qgis.Success

# ── QDialog.Accepted compatibility ───────────────────────────────────────────
# In PyQt6 the enum was moved: QDialog.DialogCode.Accepted
# qgis.PyQt handles most of this, but we guard here just in case.
try:
    _ACCEPTED = QDialog.DialogCode.Accepted   # PyQt6
except AttributeError:
    _ACCEPTED = QDialog.Accepted              # PyQt5

class DependencyManager:
    def __init__(self, iface, plugin_name, dependencies):
        self.iface = iface
        self.plugin_name = plugin_name
        self.dependencies = dependencies
        self._python_exe = self._get_python_executable()

    def _get_python_executable(self):
        """Locates the correct Python executable for the running QGIS install.

        sys.executable in QGIS points to qgis.exe / qgis-bin.exe, NOT python.
        We must search known locations explicitly.
        """
        if os.name != 'nt':
            # On Linux/macOS sys.executable is reliable
            return sys.executable

        # 1) Check alongside sys.executable (works for some QGIS builds)
        base = os.path.dirname(sys.executable)
        for name in ('python3.exe', 'python.exe'):
            candidate = os.path.join(base, name)
            if os.path.exists(candidate):
                return candidate

        # 2) OSGeo4W / standalone: Python lives under apps/PythonXXX
        #    Try OSGEO4W_ROOT env var first, then walk up from sys.executable
        osgeo_roots = []
        env_root = os.environ.get('OSGEO4W_ROOT')
        if env_root:
            osgeo_roots.append(env_root)

        # Walk up from sys.executable to find the install root
        # e.g. C:\Program Files\QGIS 3.x\bin\qgis.exe -> C:\Program Files\QGIS 3.x
        path = sys.executable
        for _ in range(4):
            path = os.path.dirname(path)
            osgeo_roots.append(path)

        for root in osgeo_roots:
            for ver in ('Python312', 'Python311', 'Python310', 'Python39', 'Python38'):
                candidate = os.path.join(root, 'apps', ver, 'python.exe')
                if os.path.exists(candidate):
                    return candidate

        # 3) Last resort: derive from the path of a known stdlib module
        import importlib.util
        spec = importlib.util.find_spec('os')
        if spec and spec.origin:
            # spec.origin -> ...Python312\Lib\os.py  =>  go up two levels
            py_dir = os.path.dirname(os.path.dirname(spec.origin))
            candidate = os.path.join(py_dir, 'python.exe')
            if os.path.exists(candidate):
                return candidate

        QgsMessageLog.logMessage(
            "Could not locate Python executable. Dependency install may fail.",
            self.plugin_name, MsgLevel.Warning)
        return 'python.exe'

    def check_missing(self):
        """Return missing dependency names."""
        missing = []
        for pip_name, import_name in self.dependencies.items():
            try:
                __import__(import_name)
            except ImportError:
                missing.append(pip_name)
        return missing

    def check_and_install(self):
        missing = self.check_missing()
        if not missing:
            return True

        dialog = DependencyInstallDialog(self.iface.mainWindow(), missing, self.plugin_name)
        if dialog.exec() == _ACCEPTED:
            return self._install_packages(missing)
        return False

    def _install_packages(self, packages):
        startupinfo = None

        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        progress = QProgressDialog("Installing dependencies...", "Cancel", 0, len(packages), self.iface.mainWindow())
        progress.setAutoClose(True)
        progress.show()

        for i, pkg in enumerate(packages):
            if progress.wasCanceled(): 
                break
                
            progress.setLabelText(f"Downloading and installing {pkg}...")
            progress.setValue(i)
            QApplication.processEvents()

            try:
                subprocess.run(
                    [self._python_exe, "-m", "pip", "install", "--user", pkg],
                    startupinfo=startupinfo,
                    capture_output=True,
                    check=True,
                    text=True
                )
            except Exception as e:
                progress.close()
                QgsMessageLog.logMessage(f"Error installing {pkg}: {str(e)}", self.plugin_name, MsgLevel.Critical)
                QMessageBox.critical(self.iface.mainWindow(), "Install error", f"Failure in {pkg}: {str(e)}")
                return False

        progress.setValue(len(packages))
        progress.close()
        QApplication.processEvents()

        QMessageBox.information(self.iface.mainWindow(), "Success", "Dependencies installed successfully!")
        return True

class DependencyInstallDialog(QDialog):
    """Interface"""
    def __init__(self, parent, packages, plugin_name):
        super().__init__(parent)
        self.setWindowTitle(f"{plugin_name} - Dependencies")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
       
        layout.addWidget(QLabel(f"<h3>📦 Missing components</h3>"
                                f"The <b>{plugin_name}</b> plugin requires additional packages:"
                                f"<ul style='color: #2980b9;'>{''.join(f'<li>{p}</li>' for p in packages)}</ul>"
                                f"<p><small>This will be installed on your python environment using pip.</small></p>"))

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        btn_install = QPushButton("Install now")
        btn_install.setDefault(True)
        btn_install.setStyleSheet("background-color: #3498db; color: white; padding: 6px; font-weight: bold;")
        btn_install.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_install)
        layout.addLayout(btn_layout)
