import importlib
import json
import re
import sys
import os
import shutil
import site
import subprocess
from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtWidgets import (
    QMessageBox, QProgressDialog, QApplication, QDialog,
    QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QTextEdit
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

#: Basenames that plausibly belong to a Python interpreter. sys.executable is
#: only trusted when it matches: inside QGIS it is frequently the QGIS binary
#: itself (qgis-bin.exe on Windows, QGIS-final-X_Y_Z inside QGIS.app on macOS),
#: and running "<that> -m pip" starts a second QGIS instead of installing.
_PYTHON_EXE_NAME = re.compile(r"^python(\d+(\.\d+)?)?(\.exe)?$", re.IGNORECASE)

#: Printed by a candidate interpreter so we can tell whether packages it
#: installs would actually be importable from the QGIS session.
_PROBE = (
    "import importlib.util,json,site,sys;"
    "print(json.dumps({'v': list(sys.version_info[:2]),"
    "'user_site': site.getusersitepackages(),"
    "'user_site_enabled': bool(site.ENABLE_USER_SITE),"
    "'pip': importlib.util.find_spec('pip') is not None}))"
)

_PROBE_TIMEOUT = 20
_INSTALL_TIMEOUT = 900


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
        #: Resolved lazily: probing interpreters costs subprocesses, and the
        #: common case (everything already importable) never needs one.
        self._python_exe = None
        self._python_exe_resolved = False
        self._ensure_user_site_on_path()

    # ------------------------------------------------------------------
    # Interpreter discovery
    # ------------------------------------------------------------------

    def _log(self, message, level=MsgLevel.Info):
        QgsMessageLog.logMessage(message, self.plugin_name, level)

    def _bundle_root(self):
        """The QGIS.app/Contents directory, or None when not on macOS."""
        if sys.platform != "darwin":
            return None
        return os.path.dirname(os.path.realpath(sys.prefix))

    def _is_bundled(self, candidate):
        root = self._bundle_root()
        if not root:
            return False
        return os.path.realpath(candidate).startswith(root + os.sep)

    def _child_env(self, candidate):
        """Environment for an interpreter we are about to spawn.

        Normally the inherited environment is right, so we return None and let
        subprocess pass it through untouched. The exception is the Python
        shipped inside QGIS.app: it is relocatable only when PYTHONHOME points
        at the bundled framework directory, and QGIS sets that in-process, so
        it is absent from os.environ and a child dies with "No module named
        'encodings'". The Contents/MacOS/python wrapper sets PYTHONHOME itself
        and simply overrides this value.

        The override is limited to interpreters inside the bundle. An
        interpreter from elsewhere is instead given an environment with any
        inherited PYTHONHOME removed, so it starts normally and is judged on
        its version and user site-packages rather than dying on a PYTHONHOME
        that was never meant for it.
        """
        if sys.platform != "darwin":
            return None

        env = os.environ.copy()
        if self._is_bundled(candidate):
            env.setdefault("PYTHONHOME", sys.prefix)
        else:
            env.pop("PYTHONHOME", None)
        return env

    def _windows_candidates(self):
        """Interpreters belonging to an OSGeo4W / standalone QGIS install."""
        candidates = []

        base = os.path.dirname(sys.executable)
        for name in ('python3.exe', 'python.exe'):
            candidates.append(os.path.join(base, name))

        osgeo_roots = []
        env_root = os.environ.get('OSGEO4W_ROOT')
        if env_root:
            osgeo_roots.append(env_root)

        path = sys.executable
        for _ in range(4):
            path = os.path.dirname(path)
            osgeo_roots.append(path)

        for root in osgeo_roots:
            for ver in ('Python313', 'Python312', 'Python311', 'Python310',
                        'Python39', 'Python38'):
                candidates.append(os.path.join(root, 'apps', ver, 'python.exe'))

        import importlib.util
        spec = importlib.util.find_spec('os')
        if spec and spec.origin:
            py_dir = os.path.dirname(os.path.dirname(spec.origin))
            candidates.append(os.path.join(py_dir, 'python.exe'))

        return candidates

    def _posix_candidates(self):
        """Interpreters belonging to a Linux install or a macOS .app bundle.

        On macOS sys.prefix is <bundle>/Contents/Frameworks and sys.executable
        lives in <bundle>/Contents/MacOS, so both lead to the bundled
        interpreter. 'python' is tried before 'pythonX.Y' because current
        bundles ship 'python' as a wrapper script that sets PYTHONHOME, while
        the bare 'pythonX.Y' next to it cannot start on its own. The 'bin'
        subdirectory covers older .dmg builds laid out as
        Contents/MacOS/bin/python3.
        """
        candidates = []
        versioned = "python{}.{}".format(*sys.version_info[:2])

        roots = []
        if sys.executable:
            roots.append(os.path.dirname(sys.executable))
        prefix = os.path.realpath(sys.prefix)
        roots.append(prefix)
        roots.append(os.path.join(os.path.dirname(prefix), 'MacOS'))
        try:
            from qgis.core import QgsApplication
            roots.append(QgsApplication.prefixPath())
        except Exception as e:
            # Only ever an extra place to look: the roots derived from
            # sys.executable and sys.prefix above already cover every layout
            # we know of, so losing this one is not worth failing over.
            self._log(f"Could not read the QGIS prefix path, continuing "
                      f"without it: {e}")

        for root in roots:
            if not root:
                continue
            for sub in ('', 'bin'):
                for name in ('python', 'python3', versioned):
                    candidates.append(os.path.join(root, sub, name))

        candidates.append(shutil.which('python3'))
        candidates.append(shutil.which('python'))
        return candidates

    def _candidate_interpreters(self):
        candidates = []

        exe = sys.executable or ""
        if exe and _PYTHON_EXE_NAME.match(os.path.basename(exe)):
            candidates.append(exe)

        if os.name == 'nt':
            candidates.extend(self._windows_candidates())
        else:
            candidates.extend(self._posix_candidates())

        ordered = []
        for path in candidates:
            if not path:
                continue
            path = os.path.normpath(path)
            if path not in ordered and os.path.isfile(path):
                ordered.append(path)
        return ordered

    def _probe(self, candidate):
        """Return the candidate's interpreter facts, or None if unusable."""
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            result = subprocess.run(
                [candidate, "-c", _PROBE],
                capture_output=True, text=True, shell=False,  # nosec B603
                startupinfo=startupinfo, env=self._child_env(candidate),
                stdin=subprocess.DEVNULL, timeout=_PROBE_TIMEOUT,
            )
        except Exception as e:
            self._log(f"Interpreter {candidate} could not be run: {e}")
            return None

        if result.returncode != 0:
            self._log(f"Interpreter {candidate} exited with "
                      f"{result.returncode}: {(result.stderr or '').strip()[:200]}")
            return None
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            self._log(f"Interpreter {candidate} returned unreadable probe "
                      f"output: {result.stdout.strip()[:200]}")
            return None

    def _get_python_executable(self):
        """Find an interpreter whose installs this QGIS session can import.

        Being the right interpreter matters more than being *an* interpreter:
        installing into, say, the system python3's user site-packages appears
        to succeed and leaves the plugin exactly as broken as before, because
        QGIS runs a different Python and never looks there. A candidate is
        therefore accepted only when its version and its user site-packages
        directory match the interpreter currently running QGIS.
        """
        if self._python_exe_resolved:
            return self._python_exe

        self._python_exe_resolved = True
        want_version = list(sys.version_info[:2])
        want_user_site = self._get_user_site_packages()
        fallback = None

        for candidate in self._candidate_interpreters():
            info = self._probe(candidate)
            if info is None:
                continue
            if info.get('v') != want_version:
                self._log(f"Skipping {candidate}: Python "
                          f"{'.'.join(str(p) for p in info.get('v', []))} does "
                          f"not match QGIS's "
                          f"{'.'.join(str(p) for p in want_version)}")
                continue
            if want_user_site and info.get('user_site') != want_user_site:
                self._log(f"Skipping {candidate}: installs would land in "
                          f"{info.get('user_site')}, which QGIS does not read")
                continue
            if not info.get('pip'):
                self._log(f"{candidate} matches but has no pip; keeping it as "
                          f"a fallback")
                fallback = fallback or candidate
                continue
            self._log(f"Using Python interpreter: {candidate}")
            self._python_exe = candidate
            return candidate

        if fallback and self._bootstrap_pip(fallback):
            self._python_exe = fallback
            return fallback

        self._log("No usable Python interpreter found for installing "
                  "dependencies.", MsgLevel.Warning)
        return None

    def _bootstrap_pip(self, candidate):
        """Last resort for installs that ship Python without pip."""
        self._log(f"Trying to bootstrap pip into {candidate} via ensurepip")
        try:
            subprocess.run(
                [candidate, "-m", "ensurepip", "--user"],
                capture_output=True, text=True, check=True,  # nosec B603
                shell=False, env=self._child_env(candidate),
                stdin=subprocess.DEVNULL, timeout=_INSTALL_TIMEOUT,
            )
        except Exception as e:
            self._log(f"ensurepip failed on {candidate}: {e}", MsgLevel.Warning)
            return False
        self._log(f"pip bootstrapped into {candidate}")
        return True

    def _best_known_interpreter(self):
        """A path to show the user when automatic installation is impossible.

        On macOS an unverified candidate from outside the bundle is worse than
        no answer at all: telling the user to run the system python3 is the
        very mistake that leaves the packages installed somewhere QGIS cannot
        see. So only bundled candidates are offered there, falling back to the
        bundle's conventional wrapper path.
        """
        if self._python_exe:
            return self._python_exe

        candidates = self._candidate_interpreters()
        if sys.platform == 'darwin':
            for candidate in candidates:
                if self._is_bundled(candidate):
                    return candidate
            root = self._bundle_root()
            return (os.path.join(root, 'MacOS', 'python') if root
                    else "/Applications/QGIS.app/Contents/MacOS/python")

        return (next(iter(candidates), None)
                or ("python.exe" if os.name == 'nt' else "python3"))

    # ------------------------------------------------------------------
    # Import path
    # ------------------------------------------------------------------

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
            self._log(f"Could not determine user site-packages: {e}",
                      MsgLevel.Warning)
        return None

    def _ensure_user_site_on_path(self):
        user_site = self._get_user_site_packages()
        if not user_site:
            return
        if user_site not in sys.path:
            sys.path.insert(0, user_site)
            self._log(f"Added user site-packages to sys.path: {user_site}")
        try:
            # addsitedir also processes any .pth files, which several of the
            # pystac/pydantic wheels rely on.
            site.addsitedir(user_site)
        except Exception as e:
            self._log(f"Could not register {user_site} as a site directory: {e}",
                      MsgLevel.Warning)
        # A directory pip has just created is still remembered as missing by
        # the import machinery; without this the fresh packages stay
        # unimportable until QGIS restarts.
        importlib.invalidate_caches()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        if self._get_python_executable() is None:
            self._show_manual_instructions(
                missing,
                "The plugin could not find the Python interpreter that runs "
                "QGIS, so it cannot install the packages for you."
            )
            return False

        success = self._install_packages(missing)

        if success:
            self._ensure_user_site_on_path()

            still_missing = self.check_missing()
            if still_missing:
                self._log(
                    f"Packages installed but still not importable: "
                    f"{still_missing}. A QGIS restart may be required.",
                    MsgLevel.Warning)
                QMessageBox.warning(
                    self.iface.mainWindow(),
                    "Restart required",
                    "The packages were installed but could not be loaded into the "
                    "current session.\n\nPlease restart QGIS and open the plugin again."
                )
                return False

        return success

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def _run_pip(self, args, startupinfo):
        return subprocess.run(
            args, startupinfo=startupinfo, capture_output=True,
            check=True, text=True, shell=False,  # nosec B603
            env=self._child_env(self._python_exe), stdin=subprocess.DEVNULL,
            timeout=_INSTALL_TIMEOUT,
        )

    def _pip_install(self, pkg, startupinfo):
        """Install a single package with --user, with two fallbacks.

        --break-system-packages covers PEP 668 "externally managed
        environment" distros (Debian 12+/Ubuntu 23.04+ and newer) where a
        plain pip install is refused outright; dropping --user covers
        environments where user site-packages are disabled, such as a
        virtualenv. --user still confines the install to the user's own
        site-packages, so the normal path stays as safe as before.

        Security notes: the argument vector is passed as a list with
        shell=False, so no shell parsing occurs; `pkg` is additionally
        validated against _SAFE_PKG_NAME, so it cannot become a pip option, a
        URL or a local path; and the interpreter has been verified to be the
        one QGIS itself runs. Installation only happens after explicit user
        consent in DependencyInstallDialog. stdin is closed so a prompting pip
        can never hang the QGIS main thread.
        """
        if not _SAFE_PKG_NAME.match(pkg):
            raise ValueError("Unsafe package name: {!r}".format(pkg))

        args = [self._python_exe, "-m", "pip", "install",
                "--disable-pip-version-check", "--user", pkg]
        try:
            self._run_pip(args, startupinfo)
            return
        except subprocess.CalledProcessError as exc:
            # Python unbinds the `as` name once the block exits, so hold on to
            # the failure itself: it carries pip's stderr, which is the only
            # useful thing to show when none of the retries below apply.
            failure = exc
            stderr = exc.stderr or ""

        if "externally-managed-environment" in stderr:
            self._run_pip(args + ["--break-system-packages"], startupinfo)
            return

        if "user site-packages are not visible" in stderr or \
                "Can not perform a '--user' install" in stderr:
            self._log("User installs are unavailable here; installing into "
                      "the active environment instead.", MsgLevel.Warning)
            self._run_pip([a for a in args if a != "--user"], startupinfo)
            return

        raise failure

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
                self._log(f"Error installing {pkg}: {err}", MsgLevel.Critical)
                self._show_manual_instructions(
                    packages, f"Failed to install '{pkg}'.", err)
                return False
            except subprocess.TimeoutExpired:
                progress.close()
                self._log(f"Timed out installing {pkg}", MsgLevel.Critical)
                self._show_manual_instructions(
                    packages, f"Installing '{pkg}' took too long and was "
                              f"stopped.")
                return False
            except Exception as e:
                progress.close()
                self._log(f"Unexpected error installing {pkg}: {e}",
                          MsgLevel.Critical)
                self._show_manual_instructions(
                    packages, f"Could not install '{pkg}'.", str(e))
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

    # ------------------------------------------------------------------
    # Manual fallback
    # ------------------------------------------------------------------

    def _manual_command(self, packages):
        exe = self._best_known_interpreter()
        quoted = f'"{exe}"' if " " in exe else exe
        return f"{quoted} -m pip install --user {' '.join(packages)}"

    def _show_manual_instructions(self, packages, reason, details=None):
        dialog = DependencyManualDialog(
            self.iface.mainWindow(), self.plugin_name, reason,
            self._manual_command(packages), details)
        dialog.exec()


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


class DependencyManualDialog(QDialog):
    """Shown when automatic installation is impossible or has failed.

    Hands the user the exact command for *their* QGIS install rather than a
    generic one, since picking the wrong interpreter is the usual reason a
    manual attempt appears to work and changes nothing.
    """

    def __init__(self, parent, plugin_name, reason, command, details=None):
        super().__init__(parent)
        self.setWindowTitle(f"{plugin_name} - Manual installation")
        self.setMinimumWidth(560)
        self._command = command
        layout = QVBoxLayout(self)

        if os.name == 'nt':
            where = ("Open the <b>OSGeo4W Shell</b> as Administrator and run:")
        elif sys.platform == 'darwin':
            where = ("Open <b>Terminal</b> and run the command below. It uses the "
                     "Python bundled inside QGIS &mdash; the system "
                     "<code>python3</code> installs into a location QGIS never "
                     "reads:")
        else:
            where = "Open a terminal and run:"

        layout.addWidget(QLabel(
            f"<h3>Automatic installation failed</h3>"
            f"<p>{reason}</p><p>{where}</p>"
        ))

        cmd_box = QTextEdit()
        cmd_box.setPlainText(command)
        cmd_box.setReadOnly(True)
        cmd_box.setFixedHeight(70)
        cmd_box.setStyleSheet("font-family: monospace;")
        layout.addWidget(cmd_box)

        layout.addWidget(QLabel(
            "<small>Restart QGIS afterwards, then open the plugin again.</small>"))

        if details:
            detail_box = QTextEdit()
            detail_box.setPlainText(details[:2000])
            detail_box.setReadOnly(True)
            detail_box.setFixedHeight(110)
            detail_box.setStyleSheet("font-family: monospace; color:#c0392b;")
            layout.addWidget(detail_box)

        btn_layout = QHBoxLayout()
        self._btn_copy = QPushButton("Copy command")
        self._btn_copy.clicked.connect(self._copy)

        btn_close = QPushButton("Close")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_copy)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _copy(self):
        QApplication.clipboard().setText(self._command)
        self._btn_copy.setText("Copied")
