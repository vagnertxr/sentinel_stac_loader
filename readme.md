# Sentinel STAC Loader
0.1

Plugin para QGIS destinado à busca e carregamento de produtos Sentinel-2 L2A via API STAC do Microsoft Planetary Computer.

Desenvolvido com Python e com a ferramenta QGIS Plugin Builder.

Faça download do .zip e descompacte na pasta de plugins do seu QGIS
(no Windows está em "AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins")


## Dependências
O plugin requer as seguintes bibliotecas Python:
- `pystac-client`
- `planetary-computer`
- `shapely`

## Instalação de Dependências (Windows)
No Windows, utilize o **OSGeo4W Shell** com privilégios de administrador:

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