"""Places anywhere in the world: countries, their main job cities, a few regions, and Remote.

Used for the suggestions while you type a place, for the location each job site understands, for narrower
searches (a country is split into its cities), for the Indeed / Glassdoor site of a country, and for checking
whether a job's location is one of your places. A place that is not listed still works: it is searched as
typed and matched by its name.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

# country, ISO code, region, other names, cities (most jobs first; "A|B" = A, also written B),
# extra words seen in job locations (states, provinces)
_C = [
    # Europe
    ("Germany", "DE", "Europe", ["Deutschland"], "Berlin;Munich|München;Hamburg;Frankfurt|Frankfurt am Main;Cologne|Köln;Stuttgart;Düsseldorf;Leipzig;Dresden;Karlsruhe;Hannover;Nuremberg|Nürnberg;Bonn;Essen;Dortmund;Mannheim;Heidelberg;Potsdam;Aachen;Bremen;Freiburg;Darmstadt;Wiesbaden;Mainz;Jena;Ulm;Augsburg;Regensburg;Kiel;Münster;Erlangen;Walldorf;Ingolstadt;Wolfsburg;Braunschweig;Göttingen;Bochum;Bielefeld;Konstanz;Tübingen;Würzburg;Garching", "bayern;bavaria;baden-wurttemberg;nordrhein-westfalen;hessen;sachsen;niedersachsen;brandenburg"),
    ("Austria", "AT", "Europe", ["Österreich"], "Vienna|Wien;Graz;Linz;Salzburg;Innsbruck;Klagenfurt", ""),
    ("Switzerland", "CH", "Europe", ["Schweiz", "Suisse"], "Zurich|Zürich;Geneva|Genève;Basel;Lausanne;Bern;Zug;Lucerne|Luzern;St. Gallen", ""),
    ("United Kingdom", "GB", "Europe", ["UK", "England", "Great Britain", "Scotland", "Wales"], "London;Manchester;Cambridge;Oxford;Edinburgh;Bristol;Birmingham;Glasgow;Leeds;Belfast;Newcastle;Reading;Nottingham;Sheffield;Liverpool", ""),
    ("Ireland", "IE", "Europe", ["Éire"], "Dublin;Cork;Galway;Limerick", ""),
    ("Netherlands", "NL", "Europe", ["Nederland", "Holland"], "Amsterdam;Rotterdam;Eindhoven;Utrecht;The Hague|Den Haag;Delft;Groningen", ""),
    ("Belgium", "BE", "Europe", ["Belgique", "België"], "Brussels|Bruxelles;Antwerp|Antwerpen;Ghent|Gent;Leuven;Liège", ""),
    ("Luxembourg", "LU", "Europe", [], "Luxembourg;Esch-sur-Alzette", ""),
    ("France", "FR", "Europe", [], "Paris;Lyon;Toulouse;Bordeaux;Lille;Nantes;Marseille;Grenoble;Nice;Sophia Antipolis;Montpellier;Rennes;Strasbourg", ""),
    ("Spain", "ES", "Europe", ["España"], "Madrid;Barcelona;Valencia;Seville|Sevilla;Málaga;Bilbao", ""),
    ("Portugal", "PT", "Europe", [], "Lisbon|Lisboa;Porto;Braga;Coimbra", ""),
    ("Italy", "IT", "Europe", ["Italia"], "Milan|Milano;Rome|Roma;Turin|Torino;Bologna;Florence|Firenze;Naples|Napoli", ""),
    ("Denmark", "DK", "Europe", ["Danmark"], "Copenhagen|København;Aarhus;Odense", ""),
    ("Sweden", "SE", "Europe", ["Sverige"], "Stockholm;Gothenburg|Göteborg;Malmö;Uppsala;Lund", ""),
    ("Norway", "NO", "Europe", ["Norge"], "Oslo;Bergen;Trondheim;Stavanger", ""),
    ("Finland", "FI", "Europe", ["Suomi"], "Helsinki;Espoo;Tampere;Oulu", ""),
    ("Poland", "PL", "Europe", ["Polska"], "Warsaw|Warszawa;Kraków|Krakow;Wrocław|Wroclaw;Gdańsk|Gdansk;Poznań|Poznan;Łódź|Lodz", ""),
    ("Czechia", "CZ", "Europe", ["Czech Republic"], "Prague|Praha;Brno;Ostrava", ""),
    ("Hungary", "HU", "Europe", ["Magyarország"], "Budapest;Debrecen", ""),
    ("Romania", "RO", "Europe", ["România"], "Bucharest|București;Cluj-Napoca;Iași;Timișoara", ""),
    ("Bulgaria", "BG", "Europe", [], "Sofia;Plovdiv;Varna", ""),
    ("Greece", "GR", "Europe", ["Hellas"], "Athens;Thessaloniki", ""),
    ("Croatia", "HR", "Europe", ["Hrvatska"], "Zagreb;Split", ""),
    ("Slovenia", "SI", "Europe", [], "Ljubljana", ""),
    ("Slovakia", "SK", "Europe", [], "Bratislava;Košice", ""),
    ("Estonia", "EE", "Europe", ["Eesti"], "Tallinn;Tartu", ""),
    ("Latvia", "LV", "Europe", [], "Riga", ""),
    ("Lithuania", "LT", "Europe", [], "Vilnius;Kaunas", ""),
    ("Serbia", "RS", "Europe", [], "Belgrade|Beograd;Novi Sad", ""),
    ("Ukraine", "UA", "Europe", [], "Kyiv|Kiev;Lviv;Kharkiv;Odesa", ""),
    ("Turkey", "TR", "Europe", ["Türkiye"], "Istanbul;Ankara;Izmir", ""),
    # North America
    ("United States", "US", "North America", ["USA", "US", "U.S.", "United States of America"], "New York|New York, NY;San Francisco;Seattle;Boston;Austin;Chicago;Los Angeles;San Jose;Washington, DC;Atlanta;Denver;Dallas;Houston;San Diego;Raleigh;Pittsburgh;Philadelphia;Miami;Minneapolis;Detroit;Phoenix;Portland;Salt Lake City", "alabama;alaska;arizona;arkansas;california;colorado;connecticut;delaware;florida;georgia;hawaii;idaho;illinois;indiana;iowa;kansas;kentucky;louisiana;maine;maryland;massachusetts;michigan;minnesota;mississippi;missouri;montana;nebraska;nevada;new hampshire;new jersey;new mexico;north carolina;north dakota;ohio;oklahoma;oregon;pennsylvania;rhode island;south carolina;south dakota;tennessee;texas;utah;vermont;virginia;west virginia;wisconsin;wyoming"),
    ("Canada", "CA", "North America", [], "Toronto;Vancouver;Montreal|Montréal;Ottawa;Calgary;Waterloo;Edmonton;Quebec City|Québec;Winnipeg;Halifax;Kitchener;Mississauga;Victoria", "ontario;quebec;british columbia;alberta;manitoba;saskatchewan;nova scotia;new brunswick;, on;, bc;, qc;, ab"),
    ("Mexico", "MX", "North America", ["México"], "Mexico City|Ciudad de México;Guadalajara;Monterrey;Querétaro", ""),
    # South America
    ("Brazil", "BR", "South America", ["Brasil"], "São Paulo|Sao Paulo;Rio de Janeiro;Belo Horizonte;Curitiba;Porto Alegre;Florianópolis", ""),
    ("Argentina", "AR", "South America", [], "Buenos Aires;Córdoba;Rosario", ""),
    ("Chile", "CL", "South America", [], "Santiago", ""),
    ("Colombia", "CO", "South America", [], "Bogotá|Bogota;Medellín|Medellin;Cali", ""),
    ("Peru", "PE", "South America", ["Perú"], "Lima", ""),
    # Asia
    ("India", "IN", "Asia", ["Bharat"], "Bengaluru|Bangalore;Hyderabad;Pune;Chennai;Mumbai;Delhi|New Delhi;Gurugram|Gurgaon;Noida;Kolkata;Ahmedabad;Kochi;Coimbatore;Jaipur;Chandigarh;Thiruvananthapuram", "karnataka;telangana;maharashtra;tamil nadu;haryana;uttar pradesh;west bengal;kerala;gujarat"),
    ("Pakistan", "PK", "Asia", [], "Karachi;Lahore;Islamabad;Rawalpindi;Faisalabad", ""),
    ("Bangladesh", "BD", "Asia", [], "Dhaka;Chattogram|Chittagong;Sylhet;Khulna;Rajshahi", ""),
    ("Sri Lanka", "LK", "Asia", [], "Colombo;Kandy", ""),
    ("Nepal", "NP", "Asia", [], "Kathmandu;Lalitpur", ""),
    ("China", "CN", "Asia", [], "Shanghai;Beijing;Shenzhen;Hangzhou;Guangzhou;Chengdu", ""),
    ("Hong Kong", "HK", "Asia", [], "Hong Kong", ""),
    ("Taiwan", "TW", "Asia", [], "Taipei;Hsinchu", ""),
    ("Japan", "JP", "Asia", ["Nippon"], "Tokyo;Osaka;Yokohama;Kyoto;Fukuoka;Nagoya", ""),
    ("South Korea", "KR", "Asia", ["Korea"], "Seoul;Busan;Pangyo", ""),
    ("Singapore", "SG", "Asia", [], "Singapore", ""),
    ("Malaysia", "MY", "Asia", [], "Kuala Lumpur;Penang;Cyberjaya", ""),
    ("Indonesia", "ID", "Asia", [], "Jakarta;Bandung;Surabaya", ""),
    ("Philippines", "PH", "Asia", [], "Manila;Makati;Cebu;Taguig", ""),
    ("Vietnam", "VN", "Asia", ["Viet Nam"], "Ho Chi Minh City;Hanoi;Da Nang", ""),
    ("Thailand", "TH", "Asia", [], "Bangkok;Chiang Mai", ""),
    # Middle East
    ("United Arab Emirates", "AE", "Middle East", ["UAE"], "Dubai;Abu Dhabi;Sharjah", ""),
    ("Saudi Arabia", "SA", "Middle East", ["KSA"], "Riyadh;Jeddah;Dammam", ""),
    ("Qatar", "QA", "Middle East", [], "Doha", ""),
    ("Kuwait", "KW", "Middle East", [], "Kuwait City", ""),
    ("Oman", "OM", "Middle East", [], "Muscat", ""),
    ("Bahrain", "BH", "Middle East", [], "Manama", ""),
    ("Israel", "IL", "Middle East", [], "Tel Aviv;Jerusalem;Haifa", ""),
    ("Jordan", "JO", "Middle East", [], "Amman", ""),
    # Africa
    ("South Africa", "ZA", "Africa", [], "Johannesburg;Cape Town;Durban;Pretoria", ""),
    ("Nigeria", "NG", "Africa", [], "Lagos;Abuja", ""),
    ("Kenya", "KE", "Africa", [], "Nairobi;Mombasa", ""),
    ("Egypt", "EG", "Africa", [], "Cairo;Alexandria", ""),
    ("Morocco", "MA", "Africa", ["Maroc"], "Casablanca;Rabat;Marrakech", ""),
    ("Ghana", "GH", "Africa", [], "Accra;Kumasi", ""),
    # Oceania
    ("Australia", "AU", "Oceania", [], "Sydney;Melbourne;Brisbane;Perth;Adelaide;Canberra;Gold Coast;Hobart", "new south wales;victoria;queensland;western australia;south australia;tasmania;, nsw;, vic;, qld"),
    ("New Zealand", "NZ", "Oceania", ["Aotearoa"], "Auckland;Wellington;Christchurch;Hamilton;Dunedin", ""),
]

# Indeed has a site per country; Glassdoor for some. Anything else uses the international one.
INDEED_CODES = {"AR", "AU", "AT", "BH", "BE", "BR", "CA", "CL", "CN", "CO", "CZ", "DK", "EG", "FI", "FR", "DE", "GR",
                "HK", "HU", "IN", "ID", "IE", "IL", "IT", "JP", "KW", "LU", "MY", "MX", "MA", "NL", "NZ", "NG", "NO",
                "OM", "PK", "PE", "PH", "PL", "PT", "QA", "RO", "SA", "SG", "ZA", "KR", "ES", "SE", "CH", "TW", "TH",
                "TR", "UA", "AE", "VN"}
GLASSDOOR = {"DE": "www.glassdoor.de", "GB": "www.glassdoor.co.uk", "CA": "www.glassdoor.ca", "AU": "www.glassdoor.com.au",
             "IN": "www.glassdoor.co.in", "FR": "www.glassdoor.fr", "NL": "www.glassdoor.nl", "IE": "www.glassdoor.ie",
             "SG": "www.glassdoor.sg", "AT": "www.glassdoor.at", "CH": "www.glassdoor.ch", "ES": "www.glassdoor.es",
             "IT": "www.glassdoor.it", "BR": "www.glassdoor.com.br", "MX": "www.glassdoor.com.mx",
             "NZ": "www.glassdoor.co.nz", "HK": "www.glassdoor.com.hk", "BE": "www.glassdoor.be"}
GERMAN_SPEAKING = {"DE", "AT", "CH"}
REGIONS = ["Europe", "North America", "South America", "Asia", "Middle East", "Africa", "Oceania"]
# what LinkedIn calls some places
LINKEDIN_NAME = {"Europe": "European Union", "Remote": "Worldwide"}


def fold(s: str) -> str:
    """lower case, no accents: München -> munchen"""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower().strip()


@dataclass
class Place:
    name: str                      # as shown and searched: "Toronto", "Canada", "Europe", "Remote"
    kind: str                      # city / country / region / remote / other
    country: str = ""              # for a city or country: the country's name
    code: str = ""                 # ISO code of the country
    region: str = ""
    aliases: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)     # a country's cities, a region's countries
    words: list[str] = field(default_factory=list)      # what a job location may say for this place

    @property
    def label(self) -> str:
        return {"city": f"{self.name}, {self.country}", "country": f"{self.name} (country)",
                "region": f"{self.name} (region)", "remote": "Remote (anywhere)"}.get(self.kind, self.name)


@lru_cache(maxsize=1)
def _index() -> tuple[dict, list]:
    by: dict[str, Place] = {}
    ordered: list[Place] = []

    def add(p: Place, *names: str):
        for n in (p.name, *names):
            by.setdefault(fold(n), p)

    countries = []
    for name, code, region, aliases, cities, extra in _C:
        city_names = [c.split("|")[0] for c in cities.split(";") if c]
        country = Place(name, "country", name, code, region, list(aliases), city_names)
        countries.append(country)
        cw = [fold(name)] + [fold(a) for a in aliases if len(a) > 3] + [w for w in extra.split(";") if w]
        for c in cities.split(";"):
            if not c:
                continue
            parts = c.split("|")
            city = Place(parts[0], "city", name, code, region, parts[1:],
                         words=[fold(x) for x in parts if "," not in x])
            cw += city.words
            ordered.append(city)
            add(city, *parts[1:])
        country.words = cw
        ordered.append(country)
        add(country, *aliases)
    for r in REGIONS:
        members = [c for c in countries if c.region == r]
        p = Place(r, "region", region=r, cities=[c.name for c in members],
                  words=[w for c in members for w in c.words] + [fold(r)] + (["emea", "european union", " eu "] if r == "Europe" else []))
        ordered.append(p)
        add(p, *(["EU", "European Union", "EMEA"] if r == "Europe" else []))
    remote = Place("Remote", "remote", aliases=["Worldwide", "Anywhere", "Work from home"])
    ordered.append(remote)
    add(remote, "Worldwide", "Anywhere", "Work from home", "Home office")
    return by, ordered


def find(name: str) -> Place:
    """The place for a name you typed; unknown names become a place of their own."""
    p = _index()[0].get(fold(name))
    if p:
        return p
    # "Toronto, Canada" / "Toronto, ON"
    head = fold(name.split(",")[0])
    p = _index()[0].get(head)
    if p and p.kind == "city":
        return p
    return Place(name.strip(), "other", words=[fold(name)])


def suggest(q: str, limit: int = 12) -> list[dict]:
    """Places that start with (then contain) what you typed: cities, countries, regions, Remote."""
    fq = fold(q)
    _, ordered = _index()
    if not fq:
        popular = ["Remote", "Germany", "Berlin", "United States", "United Kingdom", "Canada", "India", "Australia",
                   "Europe", "Netherlands", "Singapore", "Toronto"]
        out = [find(n) for n in popular]
    else:
        starts = [p for p in ordered if fold(p.name).startswith(fq) or any(fold(a).startswith(fq) for a in p.aliases)]
        rank = {"country": 0, "region": 1, "remote": 1, "city": 2}
        pos = {id(p): i for i, p in enumerate(ordered)}             # bigger job cities come first in the list
        starts.sort(key=lambda p: (not fold(p.name).startswith(fq), rank.get(p.kind, 3), pos[id(p)]))
        inside = [p for p in ordered if p not in starts and fq in fold(p.name)]
        out = starts + inside
    seen, res = set(), []
    for p in out:
        if p.name in seen:
            continue
        seen.add(p.name)
        res.append({"value": p.name, "label": p.label, "kind": p.kind, "group": p.country or p.region or ""})
        if len(res) >= limit:
            break
    return res


def is_german(name: str) -> bool:
    """A place in Germany, Austria or Switzerland: searched with German words too (Werkstudent, Praktikum)."""
    return find(name).code in GERMAN_SPEAKING


def in_country(name: str, code: str) -> bool:
    return find(name).code == code


def site_location(site: str, name: str) -> str:
    """How a job site wants the place written."""
    p = find(name)
    if site == "linkedin":
        if p.kind == "city" and "," not in name:
            return f"{p.name}, {p.country}"
        return LINKEDIN_NAME.get(p.name, p.name)
    if site in ("indeed", "glassdoor") and p.kind == "city" and p.country not in ("Germany",):
        return f"{p.name}, {p.country}" if p.code != "US" else p.name
    return p.name


def narrower(name: str) -> list[str]:
    """What a too-big search on this place is split into: a country into its cities, a region into countries."""
    p = find(name)
    if p.kind in ("country", "region"):
        return list(p.cities)
    return []


def indeed_host(name: str) -> str:
    p = find(name)
    if p.code == "US" or p.kind in ("remote", "region", "other") or not p.code:
        return "www.indeed.com"
    if p.code == "GB":
        return "uk.indeed.com"
    return f"{p.code.lower()}.indeed.com" if p.code in INDEED_CODES else "www.indeed.com"


def glassdoor_host(name: str) -> str:
    p = find(name)
    if p.kind == "region" and p.name == "Europe":
        return GLASSDOOR["DE"]
    return GLASSDOOR.get(p.code, "www.glassdoor.com")


_US_ABBR = re.compile(r",\s*(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b")


@lru_cache(maxsize=512)
def _words_re(name: str) -> Optional[re.Pattern]:
    parts = []
    for w in sorted({w.strip() if w.strip() and w.strip()[0].isalnum() else w for w in find(name).words if w}, key=len, reverse=True):
        core = re.escape(w.strip() if w.strip()[:1].isalnum() else w)
        parts.append(("(?<![a-z0-9])" if w.strip()[:1].isalnum() else "") + core + ("(?![a-z0-9])" if w.strip()[-1:].isalnum() else ""))
    return re.compile("|".join(parts)) if parts else None


def location_hits(location: str, name: str) -> bool:
    """Does a job's location (as the site wrote it) lie in this place?"""
    loc = " " + fold(location) + " "
    p = find(name)
    if p.kind == "remote":
        return bool(re.search(r"remote|home ?office|anywhere|work from home|worldwide", loc))
    rx = _words_re(name)
    if rx and rx.search(loc):
        return True
    if p.code == "US" or (p.kind == "region" and p.name == "North America"):
        if _US_ABBR.search(location or "") or re.search(r"\b(usa|u\.s\.)\b", loc):
            return True
    if p.code == "GB" and re.search(r",\s*uk\b|\buk\b", loc):
        return True
    if p.code == "DE" and re.search(r",\s*de\b|\(de\)", loc):
        return True
    return False


def country_of(location: str) -> Optional[Place]:
    """The country a job's location is in, when it can be told."""
    _, ordered = _index()
    for p in ordered:
        if p.kind == "country" and location_hits(location, p.name):
            return p
    return None
