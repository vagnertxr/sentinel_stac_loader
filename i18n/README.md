# Tradução do plugin (i18n)

O plugin usa o sistema de tradução do Qt. As strings em inglês no código são a referência; os ficheiros `.ts` guardam as traduções e compilam para `.qm`, que o QGIS carrega conforme o idioma do utilizador.

## Fluxo rápido

1. **Extrair/atualizar strings** (após alterar texto no código):
   ```bash
   cd /caminho/para/sentinel_stac_loader
   make transup
   ```
   Isto atualiza os ficheiros em `i18n/*.ts` com todas as mensagens que usam `self.tr()` ou `QCoreApplication.translate()`.

2. **Traduzir**  
   Edite os ficheiros `.ts` (por exemplo `i18n/pt.ts`) e preencha as tags `<translation>...</translation>` para cada `<source>...</source>`. Pode usar o **Qt Linguist** (`linguist`) ou editar o XML à mão.

3. **Compilar para .qm**:
   ```bash
   make transcompile
   ```
   Gera `i18n/pt.qm`, `i18n/en.qm`, etc. O plugin carrega automaticamente o ficheiro conforme o idioma do QGIS (ex.: `pt` → `i18n/pt.qm`).

## Onde estão as strings no código

- **sentinel_stac_loader.py**: menu e ação da barra de ferramentas usam `self.tr(u'...')`.
- **sentinel_stac_loader_dialog.py**: mensagens na barra de mensagens e no diálogo. Para passarem a ser traduzíveis, devem usar `QCoreApplication.translate('SentinelSTAC', '...')` ou receber um método `tr` do plugin.

O contexto das traduções é `SentinelSTAC` (segundo argumento de `translate()` e contexto usado pelo `pylupdate5`).

## Requisitos

- **pylupdate5** – extração de strings (PyQt5). Se não tiver no PATH:
  - Debian/Ubuntu: `sudo apt install pyqt5-dev-tools`
  - Fedora: `sudo dnf install pyqt5-devel`
  - Ou use o que vier com o QGIS/PyQt5.
- **lrelease** – compilação `.ts` → `.qm` (Qt Linguist):
  - Debian/Ubuntu: `sudo apt install qttools5-dev-tools`
  - Fedora: `sudo dnf install qt5-linguist`

Se o seu sistema usar `pylupdate4` em vez de `pylupdate5`, edite `scripts/update-strings.sh` e troque o comando para o que tiver instalado.

## Idiomas configurados

No `Makefile` está definido:

```make
LOCALES = en pt
```

Pode adicionar mais códigos (ex.: `es`, `de`, `fr`). Cada um terá um ficheiro `i18n/<codigo>.ts` e, após `make transcompile`, um `i18n/<codigo>.qm`. O plugin usa os dois primeiros caracteres do idioma do QGIS (ex.: `pt_BR` → `pt`) para carregar `i18n/pt.qm`.

## Resumo dos alvos Make

| Comando           | Descrição                              |
|-------------------|----------------------------------------|
| `make transup`    | Atualiza os `.ts` a partir do código   |
| `make transcompile` | Compila `.ts` → `.qm`                |
| `make transclean` | Apaga ficheiros `.qm` em `i18n/`       |
