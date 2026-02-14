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
        """Encontra o executável Python real dentro do ambiente QGIS."""
        # No Windows, sys.executable é o QGIS.exe. Precisamos do python.exe.
        if os.name == 'nt':
            python_exe = os.path.join(os.path.dirname(sys.executable), "python3.exe")
            if not os.path.exists(python_exe):
                python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
            
            # Se não achou na pasta do QGIS, tenta via variável de ambiente OSGeo4W
            if not os.path.exists(python_exe):
                root = os.environ.get('OSGEO4W_ROOT')
                if root:
                    # Tenta versões comuns do Python no OSGeo4W
                    for ver in ['Python312', 'Python311', 'Python310', 'Python39']:
                        path = os.path.join(root, 'apps', ver, 'python.exe')
                        if os.path.exists(path): return path
            return python_exe
        return sys.executable

    def check_missing(self):
        """Retorna apenas os nomes do PIP que não estão instalados."""
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

        # Diálogo de confirmação customizado
        dialog = DependencyInstallDialog(self.iface.mainWindow(), missing, self.plugin_name)
        if dialog.exec_() == QDialog.Accepted:
            return self._install_packages(missing)
        return False

    def _install_packages(self, packages):
        # 1. INICIALIZE SEMPRE COMO NONE (Isso evita o NameError)
        startupinfo = None
        
        # 2. SÓ ATRIBUA VALOR SE FOR WINDOWS
        if os.name == 'nt':
            import subprocess # Garantir que o import está acessível
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # Opcional: startupinfo.wShowWindow = 0 (SW_HIDE)

        progress = QProgressDialog("Instalando dependências...", "Cancelar", 0, len(packages), self.iface.mainWindow())
        progress.setAutoClose(True)
        progress.show()

        for i, pkg in enumerate(packages):
            if progress.wasCanceled(): 
                break
                
            progress.setLabelText(f"Baixando e instalando {pkg}...")
            progress.setValue(i)
            QApplication.processEvents()

            try:
                # O startupinfo=startupinfo agora funcionará em qualquer SO
                # Se for None (Linux), o subprocess apenas ignora.
                subprocess.run(
                    [self._python_exe, "-m", "pip", "install", "--user", pkg],
                    startupinfo=startupinfo,
                    capture_output=True,
                    check=True,
                    text=True # Útil para ler o erro se houver
                )
            except Exception as e:
                progress.close()
                QgsMessageLog.logMessage(f"Erro ao instalar {pkg}: {str(e)}", self.plugin_name, Qgis.Critical)
                QMessageBox.critical(self.iface.mainWindow(), "Erro de Instalação", f"Falha em {pkg}: {str(e)}")
                return False

        progress.setValue(len(packages))
        progress.close()
        QApplication.processEvents()

        QMessageBox.information(self.iface.mainWindow(), "Sucesso", "Dependências instaladas com sucesso!")
        return True

class DependencyInstallDialog(QDialog):
    """Interface moderna baseada na imagem de referência."""
    def __init__(self, parent, packages, plugin_name):
        super().__init__(parent)
        self.setWindowTitle(f"{plugin_name} - Dependências")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        
        # Estilização via HTML
        layout.addWidget(QLabel(f"<h3>📦 Componentes Faltando</h3>"
                                f"O plugin <b>{plugin_name}</b> requer pacotes adicionais:"
                                f"<ul style='color: #2980b9;'>{''.join(f'<li>{p}</li>' for p in packages)}</ul>"
                                f"<p><small>Isso será instalado na sua pasta de usuário via pip.</small></p>"))

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        
        btn_install = QPushButton("Instalar Agora")
        btn_install.setDefault(True)
        # Cor azul estilo 'moderno'
        btn_install.setStyleSheet("background-color: #3498db; color: white; padding: 6px; font-weight: bold;")
        btn_install.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_install)
        layout.addLayout(btn_layout)