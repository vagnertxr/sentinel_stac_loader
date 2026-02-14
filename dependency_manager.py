import sys
import os
import subprocess
from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtWidgets import (
    QMessageBox, QProgressDialog, QApplication, QDialog,
    QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)
from qgis.PyQt.QtCore import Qt

class DependencyManager:
    def __init__(self, iface, plugin_name, dependencies):
        self.iface = iface
        self.plugin_name = plugin_name
        self.dependencies = dependencies
        self._python_exe = self._get_python_executable()

    def _get_python_executable(self):
        """Looks for QGIS Python environment."""

        if os.name == 'nt':
            python_exe = os.path.join(os.path.dirname(sys.executable), "python3.exe")
            if not os.path.exists(python_exe):
                python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
            
            if not os.path.exists(python_exe):
                root = os.environ.get('OSGEO4W_ROOT')
                if root:
                    for ver in ['Python312', 'Python311', 'Python310', 'Python39']:
                        path = os.path.join(root, 'apps', ver, 'python.exe')
                        if os.path.exists(path): return path
            return python_exe
        return sys.executable

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
        if dialog.exec_() == QDialog.Accepted:
            return self._install_packages(missing)
        return False

    def _install_packages(self, packages):
        startupinfo = None
        

        if os.name == 'nt':
            import subprocess 
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
                QgsMessageLog.logMessage(f"Error installing {pkg}: {str(e)}", self.plugin_name, Qgis.Critical)
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
        self.setWindowTitle(f"{plugin_name} - Dependências")
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
        
