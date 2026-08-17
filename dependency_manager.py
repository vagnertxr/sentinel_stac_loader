import re
import sys
import os
import site
import subprocess
from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtWidgets import (
    QMessageBox, QProgressDialog, QApplication, QDialog,
    QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)
from qgis.PyQt.QtCore import Qt


class MsgLevel:
    Info     = Qgis.MessageLevel.Info
    Warning  = Qgis.MessageLevel.Warning
    Critical = Qgis.MessageLevel.Critical
    Success  = Qgis.MessageLevel.Success


_ACCEPTED = QDialog.DialogCode.Accepted


#: A dependency name must be a plain PEP 508 distribution name: letters,
#: digits and single -/_/. separators. This deliberately excludes version
#: specifiers, URLs, paths, option-like leading dashes and shell
#: metacharacters, so nothing that reaches pip can be read as anything other
#: than a package name from PyPI.
_SAFE_PKG_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


class DependencyManager:
    PLUGIN_NAME = "Quick VRT Imagery Loader"

    def __init__(self, iface, plugin_name, dependencies):
        for pip_name in dependencies:
            if not _SAFE_PKG_NAME.match(pip_name):
                raise ValueError(
                    "Refusing to manage unsafe dependency name: {!r}".format(
                        pip_name))
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
            # Resolved in-process: the interpreter running QGIS is the one
            # whose sys.path we are about to extend, so asking the local site
            # module is both correct and avoids spawning a subprocess.
            path = site.getusersitepackages()
            if isinstance(path, (list, tuple)):
                path = path[0] if path else None
            if path:
                return path.strip()
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

    def _pip_install(self, pkg, startupinfo):
        """Install a single package with --user, falling back to
        --break-system-packages on PEP 668 "externally managed environment"
        distros (Debian 12+/Ubuntu 23.04+ and newer) where a plain pip
        install is refused outright. --user still confines the install to
        the user's own site-packages, so this stays as safe as the normal path.

        Security notes: the argument vector is passed as a list with
        shell=False, so no shell parsing occurs; `pkg` is additionally
        validated against _SAFE_PKG_NAME, so it cannot become a pip option, a
        URL or a local path; and the interpreter is the one QGIS is running
        under. Installation only happens after explicit user consent in
        DependencyInstallDialog."""
        if not _SAFE_PKG_NAME.match(pkg):
            raise ValueError("Unsafe package name: {!r}".format(pkg))

        args = [self._python_exe, "-m", "pip", "install", "--user", pkg]
        try:
            # Fixed argv, shell=False, package name validated above.
            subprocess.run(
                args, startupinfo=startupinfo, capture_output=True,
                check=True, text=True, shell=False,  # nosec B603
            )
        except subprocess.CalledProcessError as e:
            if "externally-managed-environment" not in (e.stderr or ""):
                raise
            # Same argv plus one fixed literal flag; still shell=False.
            subprocess.run(
                args + ["--break-system-packages"],
                startupinfo=startupinfo, capture_output=True,
                check=True, text=True, shell=False,  # nosec B603
            )

    def _install_packages(self, packages):
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        progress = QProgressDialog(
            "Installing dependencies...", "Cancel", 0, len(packages),
            self.iface.mainWindow())
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.show()

        for i, pkg in enumerate(packages):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"Downloading and installing {pkg}...")
            progress.setValue(i)
            QApplication.processEvents()

            try:
                self._pip_install(pkg, startupinfo)
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