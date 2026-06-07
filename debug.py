import requests
from lxml import etree
from config import DATAFORDELER_USERNAME, DATAFORDELER_PASSWORD

ns = {"gdk60": "http://data.gov.dk/schemas/geodanmark60/2/gml3",
      "wfs": "http://www.opengis.net/wfs/2.0"}
params = {
    "username": DATAFORDELER_USERNAME, "password": DATAFORDELER_PASSWORD,
    "SERVICE": "WFS", "REQUEST": "GetFeature", "VERSION": "2.0.0",
    "TYPENAMES": "gdk60:Bygning",
    "NAMESPACES": "xmlns(gdk60,http://data.gov.dk/schemas/geodanmark60/2/gml3)",
    "COUNT": "1", "SRSNAME": "EPSG:25832",
    "BBOX": "725000,6176000,725500,6176500,EPSG:25832",
}
r = requests.get("https://services.datafordeler.dk/GeoDanmarkVektor/GeoDanmark60_NOHIST_GML3/1.0.0/WFS", params=params)
root = etree.fromstring(r.content)
b = root.find(".//gdk60:Bygning", ns)
for c in b.iter():
    print(etree.QName(c).localname, "->", (c.text or "").strip()[:50])

import requests
from config import DATAFORDELER_USERNAME, DATAFORDELER_PASSWORD
bbr = requests.get("https://services.datafordeler.dk/BBR/BBRPublic/1/rest/bygning",
    params={"username": DATAFORDELER_USERNAME, "password": DATAFORDELER_PASSWORD,
            "Format": "JSON", "status": "6",
            "nord": 6176500, "syd": 6176000, "oest": 725500, "vest": 725000,
            "pagesize": 1, "page": 1}).json()
print(bbr[0])