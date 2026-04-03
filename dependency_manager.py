import sys
import os
import subprocess
from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtWidgets import (
    QMessageBox, QProgressDialog, QApplication, QDialog,
    QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)
from qgis.PyQt.QtCore import Qt


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


try:
    _ACCEPTED = QDialog.DialogCode.Accepted   # PyQt6
except AttributeError:
    _ACCEPTED = QDialog.Accepted              # PyQt5


class DependencyManager:
    PLUGIN_NAME = "Quick VRT Imagery Loader"

    def __init__(self, iface, plugin_name, dependencies):
        self.iface = iface
        self.plugin_name = plugin_name
        self.dependencies = dependencies
        self._python_exe = self._get_python_executable()
        self._ensure_user_site_on_path()

    def _get_python_executable(self):
        """Locate the Python executable that belongs to the running QGIS install."""
        if os.name != 'nt':
            return sys.executable

        base = os.path.dirname(sys.executable)
        for name in ('python3.exe', 'python.exe'):
            candidate = os.path.join(base, name)
            if os.path.exists(candidate):
                return candidate

        osgeo_roots = []
        env_root = os.environ.get('OSGEO4W_ROOT')
        if env_root:
            osgeo_roots.append(env_root)

        path = sys.executable
        for _ in range(4):
            path = os.path.dirname(path)
            osgeo_roots.append(path)

        for root in osgeo_roots:
            for ver in ('Python312', 'Python311', 'Python310', 'Python39', 'Python38'):
                candidate = os.path.join(root, 'apps', ver, 'python.exe')
                if os.path.exists(candidate):
                    return candidate

        import importlib.util
        spec = importlib.util.find_spec('os')
        if spec and spec.origin:
            py_dir = os.path.dirname(os.path.dirname(spec.origin))
            candidate = os.path.join(py_dir, 'python.exe')
            if os.path.exists(candidate):
                return candidate

        QgsMessageLog.logMessage(
            "Could not locate Python executable. Dependency install may fail.",
            self.plugin_name, MsgLevel.Warning)
        return 'python.exe'

    def _get_user_site_packages(self):
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(
                [self._python_exe, '-c',
                 'import site; print(site.getusersitepackages())'],
                capture_output=True, text=True,
                startupinfo=startupinfo
            )
            path = result.stdout.strip()
            if path:
                return path
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Could not determine user site-packages: {e}",
                self.plugin_name, MsgLevel.Warning)
        return None

    def _ensure_user_site_on_path(self):
        user_site = self._get_user_site_packages()
        if user_site and user_site not in sys.path:
            sys.path.insert(0, user_site)
            QgsMessageLog.logMessage(
                f"Added user site-packages to sys.path: {user_site}",
                self.plugin_name, MsgLevel.Info)

    def check_missing(self):
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

        dialog = DependencyInstallDialog(
            self.iface.mainWindow(), missing, self.plugin_name)
        if dialog.exec() != _ACCEPTED:
            return False

        success = self._install_packages(missing)

        if success:
            self._ensure_user_site_on_path()

            still_missing = self.check_missing()
            if still_missing:
                QgsMessageLog.logMessage(
                    f"Packages installed but still not importable: {still_missing}. "
                    "A QGIS restart may be required.",
                    self.plugin_name, MsgLevel.Warning)
                QMessageBox.warning(
                    self.iface.mainWindow(),
                    "Restart required",
                    "The packages were installed but could not be loaded into the "
                    "current session.\n\nPlease restart QGIS and open the plugin again."
                )
                return False

        return success

    def _install_packages(self, packages):
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        progress = QProgressDialog(
            "Installing dependencies...", "Cancel", 0, len(packages),
            self.iface.mainWindow())
        progress.setWindowModality(Qt.WindowModality.WindowModal \
            if hasattr(Qt.WindowModality, 'WindowModal') \
            else Qt.WindowModal)
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
            except subprocess.CalledProcessError as e:
                progress.close()
                err = e.stderr or str(e)
                QgsMessageLog.logMessage(
                    f"Error installing {pkg}: {err}",
                    self.plugin_name, MsgLevel.Critical)
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "Install error",
                    f"Failed to install '{pkg}':\n\n{err[:300]}"
                )
                return False
            except Exception as e:
                progress.close()
                QgsMessageLog.logMessage(
                    f"Unexpected error installing {pkg}: {e}",
                    self.plugin_name, MsgLevel.Critical)
                QMessageBox.critical(
                    self.iface.mainWindow(), "Install error", str(e))
                return False

        progress.setValue(len(packages))
        progress.close()
        QApplication.processEvents()

        QMessageBox.information(
            self.iface.mainWindow(),
            "Success",
            "Dependencies installed successfully!\n\n"
            "The plugin is ready to use."
        )
        return True


class DependencyInstallDialog(QDialog):
    """Confirmation dialog shown before installing missing packages."""

    def __init__(self, parent, packages, plugin_name):
        super().__init__(parent)
        self.setWindowTitle(f"{plugin_name} - Dependencies")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        pkg_list = "".join(f"<li><b>{p}</b></li>" for p in packages)
        layout.addWidget(QLabel(
            f"<h3>Missing components</h3>"
            f"The <b>{plugin_name}</b> plugin requires additional packages:"
            f"<ul style='color:#2980b9;'>{pkg_list}</ul>"
            f"<p><small>They will be installed into your user Python environment "
            f"via <code>pip install --user</code> and will be available "
            f"<b>immediately</b>, without restarting QGIS.</small></p>"
        ))

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_install = QPushButton("Install now")
        btn_install.setDefault(True)
        btn_install.setStyleSheet(
            "background-color:#3498db; color:white; padding:6px; font-weight:bold;")
        btn_install.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_install)
        layout.addLayout(btn_layout)