# Quick Sentinel-2 STAC Loader
0.2

Plugin para QGIS destinado à busca e carregamento de produtos Sentinel-2 L2A via API STAC do Microsoft Planetary Computer.

Desenvolvido com Python e com a ferramenta QGIS Plugin Builder.

Faça download do .zip e utilize a opção "Install from ZIP" no Gerenciador de Plugins do QGIS ou  descompacte na pasta de plugins
(no Windows está em "%AppData%\Roaming\QGIS\QGIS3\profiles\default\python\plugins")


## Dependências
O plugin requer as seguintes bibliotecas Python:
- `pystac-client`
- `planetary-computer`
- `shapely`

## Instalação de Dependências (Windows)
No Windows, utilize o **OSGeo4W Shell**:

```bash
python3 -m pip install pystac-client planetary-computer shapely
```

## Instalação de Dependências (Linux)
Instale via terminal no interpretador Python utilizado pelo QGIS:

```bash
pip install pystac-client planetary-computer shapely
```

## Licença

GNU General Public License v2.
