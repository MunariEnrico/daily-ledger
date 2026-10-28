# Source registry — verification record

All feeds in `config/sources.yaml` were fetched and parsed on **2026-10-28** from the development environment
(User-Agent `Mozilla/5.0 (compatible; TheDailyLedger/1.0; …)`). Re-test any time with `python -m ledger sources --check`;
the Tests workflow repeats this weekly.

**Registered and working: 209 feeds.** By type: newspaper 108, official 29, broadcaster 23, magazine 19, agency 12, local 11, specialist 7.
By country/area: UK 66, IT 56, US 31, FR 21, LU 11, EU 10, DE 3, IN 3, INT 3, CN 2, JP 2, ME 1.

Intermittent during testing: Comune di Venezia (HTTP 403 on some requests), CSSF (connection resets), STATEC (empty response once).
The pipeline records such failures per run, publishes the rest, and lists unreachable sources in the editor's note and on the status page.

## Tested and not integrated

These URLs were tried and rejected: they block automated readers (HTTP 403/490), have no endpoint (404), are empty, or are stale.
Nothing was added to the registry without a successful test. Reporting from these outlets can still reach the paper through agencies and other outlets.

| Tested URL | Result |
|---|---|
| https://apnews.com/hub/business.rss | HTTP 403 |
| https://chronicle.lu/rss | HTTP 404 |
| https://curia.europa.eu/jcms/jcms/Jo2_7052/rss | HTTP 404 |
| https://delano.lu/feeds/rss | HTTP 404 |
| https://delano.lu/rss | HTTP 404 |
| https://delano.lu/rss.xml | HTTP 404 |
| https://ec.europa.eu/eurostat/en/web/rss/euro-indicators | HTTP 404 |
| https://english.kyodonews.net/rss/news.xml | HTTP 404 |
| https://feeds.a.dj.com/rss/RSSMarketsMain.xml | stale: newest entry 638.8 days old |
| https://feeds.a.dj.com/rss/RSSWorldNews.xml | stale: newest entry 638.8 days old |
| https://feeds.npr.org/1006/rss.xml | HTTP 403 |
| https://feeds.npr.org/1014/rss.xml | HTTP 403 |
| https://feeds.washingtonpost.com/rss/business | HTTP 502 |
| https://feeds.washingtonpost.com/rss/politics | HTTP 502 |
| https://gouvernement.lu/en/actualites.rss | HTTP 200 but no entries |
| https://gouvernement.lu/fr/actualites.rss | HTTP 200 but no entries |
| https://gouvernement.lu/fr/actualites.rss?format=rss | HTTP 200 but no entries |
| https://home.treasury.gov/system/files/126/ofac.xml | HTTP 403 |
| https://mattinopadova.it/feed | HTTP 404 |
| https://mattinopadova.it/rss.xml | HTTP 404 |
| https://milano.corriere.it/rss/homepage.xml | stale: newest entry 5363.8 days old |
| https://milano.repubblica.it/rss/rss2.0.xml | stale: newest entry 1047.0 days old |
| https://new.mta.info/rss.xml | HTTP 403 |
| https://nuovavenezia.it/feed | HTTP 404 |
| https://nuovavenezia.it/rss | HTTP 404 |
| https://nuovavenezia.it/rss.xml | HTTP 404 |
| https://pagellapolitica.it/feed | HTTP 200 but no entries |
| https://paperjam.lu/feed | HTTP 404 |
| https://paperjam.lu/feeds/rss | HTTP 404 |
| https://paperjam.lu/rss | HTTP 404 |
| https://paperjam.lu/rss.xml | HTTP 404 |
| https://roma.corriere.it/rss/homepage.xml | HTTP 404 |
| https://roma.repubblica.it/rss/rss2.0.xml | stale: newest entry 1047.0 days old |
| https://services.lesechos.fr/rss/les-echos-economie.xml | HTTP 403 |
| https://statistiques.public.lu/en/news.rss | HTTP 404 |
| https://tfl.gov.uk/info-for/media/press-releases/rss | HTTP 403 |
| https://tfl.gov.uk/info-for/media/press-releases?rss | HTTP 403 |
| https://thehill.com/policy/finance/feed/ | HTTP 200 but no entries |
| https://www.adnkronos.com/rss | HTTP 200 but no entries |
| https://www.agenziaentrate.gov.it/portale/rss | HTTP 200 but no entries |
| https://www.airport.lu/feed | HTTP 404 |
| https://www.ansa.it/canale_viaggiart/it/notizie_rss.xml | HTTP 404 |
| https://www.ansa.it/sito/notizie/economia/borsa/borsa_rss.xml | HTTP 404 |
| https://www.ansa.it/sito/notizie/economia/finanza/finanza_rss.xml | HTTP 404 |
| https://www.ansa.it/sito/notizie/economia/fisco/fisco_rss.xml | HTTP 404 |
| https://www.ansa.it/sito/notizie/viaggi/viaggi_rss.xml | HTTP 404 |
| https://www.ansa.it/viaggiart/it/notizie/rss.xml | HTTP 404 |
| https://www.arabnews.com/rss.xml | HTTP 403 |
| https://www.avvenire.it/rss | HTTP 404 |
| https://www.bancaditalia.it/media/comunicati/index.rss?lingua=it | HTTP 404 |
| https://www.bancaditalia.it/media/comunicati/rss.xml | HTTP 404 |
| https://www.bancaditalia.it/util/index.rss.xml?lingua=it | HTTP 404 |
| https://www.bancaditalia.it/util/index.rss?lingua=it | HTTP 404 |
| https://www.banque-france.fr/fr/rss.xml | HTTP 403 |
| https://www.bcl.lu/en/rss | HTTP 200 but no entries |
| https://www.bcl.lu/fr/media_actualites/communiques/rss.xml | HTTP 200 but no entries |
| https://www.bls.gov/feed/cpi.rss | HTTP 403 |
| https://www.bls.gov/feed/news_release.rss | HTTP 403 |
| https://www.caixinglobal.com/rss/ | HTTP 403 |
| https://www.camera.it/leg19/rss | HTTP 404 |
| https://www.chd.lu/fr/rss | HTTP 404 |
| https://www.chd.lu/rss | HTTP 200 but no entries |
| https://www.chronicle.lu/component/obrss/chronicle-lu-latest-news | HTTP 404 |
| https://www.cityam.com/category/london-news/feed/ | HTTP 404 |
| https://www.cityoflondon.gov.uk/rss | HTTP 404 |
| https://www.cnbc.com/id/100003114/device/rss/rss.html | HTTP 403 |
| https://www.cnbc.com/id/10000664/device/rss/rss.html | HTTP 403 |
| https://www.comune.milano.it/rss | HTTP 403 |
| https://www.comune.roma.it/web/it/rss.page | HTTP 404 |
| https://www.connexionfrance.com/rss | HTTP 404 |
| https://www.consilium.europa.eu/en/press/press-releases/rss | HTTP 403 |
| https://www.consob.it/rss | HTTP 404 |
| https://www.consob.it/web/area-pubblica/rss | HTTP 404 |
| https://www.corriere.it/dynamic-feed/rss/homepage.xml | HTTP 404 |
| https://www.corriere.it/rss/homepage.xml | stale: newest entry 898.3 days old |
| https://www.crainsnewyork.com/rss | HTTP 403 |
| https://www.crainsnewyork.com/rss.xml | HTTP 403 |
| https://www.cssf.lu/en/feed/ | HTTP 400 |
| https://www.dw.com/rss/rss-en-all | HTTP 404 |
| https://www.dw.com/rss/rss-en-bus | HTTP 404 |
| https://www.eba.europa.eu/rss/press-releases | HTTP 404 |
| https://www.economie.gouv.fr/rss | HTTP 200 but no entries |
| https://www.eib.org/en/rss/press.xml | HTTP 404 |
| https://www.esm.europa.eu/rss.xml | HTTP 403 |
| https://www.esma.europa.eu/press-news/esma-news/rss | HTTP 403 |
| https://www.esma.europa.eu/rss.xml | HTTP 403 |
| https://www.fanpage.it/feed/ | HTTP 403 |
| https://www.gouvernement.fr/rss | HTTP 403 |
| https://www.gov.uk/government/organisations/financial-conduct-authority.atom | stale: newest entry 33.1 days old |
| https://www.iea.org/rss/news | HTTP 403 |
| https://www.ilfoglio.it/rss.jsp?sezione=121 | HTTP 410 |
| https://www.ilgiornale.it/feed.xml | HTTP 404 |
| https://www.ilpost.it/feed/ | HTTP 403 |
| https://www.ilpost.it/rss | HTTP 404 |
| https://www.ilsole24ore.com/rss/finanza--borsa.xml | HTTP 404 |
| https://www.ilsole24ore.com/rss/norme-e-tributi--fisco.xml | HTTP 404 |
| https://www.imf.org/en/News/rss?language=eng | HTTP 403 |
| https://www.imf.org/en/news/rss | HTTP 403 |
| https://www.insee.fr/en/information/1300614?rss | HTTP 404 |
| https://www.insee.fr/fr/information/1405555?rss | HTTP 200 but no entries |
| https://www.insee.fr/fr/rss/flux.xml | HTTP 500 |
| https://www.investorschronicle.co.uk/rss | HTTP 403 |
| https://www.istat.it/comunicati-stampa/feed/ | HTTP 200 but no entries |
| https://www.istat.it/comunicato-stampa/feed/ | HTTP 403 |
| https://www.italiaoggi.it/rss | HTTP 404 |
| https://www.lanuovadivenezia.it/rss | connection error |
| https://www.lapiazzaweb.it/feed/ | HTTP 404 |
| https://www.lastampa.it/rss/economia.xml | HTTP 403 |
| https://www.lastampa.it/rss/homepage.xml | HTTP 403 |
| https://www.latribune.fr/rss/a-la-une.html | HTTP 404 |
| https://www.latribune.fr/rss/rubriques/economie.html | HTTP 404 |
| https://www.lefigaro.fr/rss/figaro_actualites.xml | HTTP 403 |
| https://www.lefigaro.fr/rss/figaro_economie.xml | HTTP 403 |
| https://www.lepoint.fr/24h-infos/rss.xml | HTTP 403 |
| https://www.lepoint.fr/economie/rss.xml | HTTP 403 |
| https://www.lepoint.fr/rss.xml | HTTP 404 |
| https://www.lesechos.fr/rss/rss_une.xml | HTTP 403 |
| https://www.lessentiel.lu/fr/rss | HTTP 404 |
| https://www.lessentiel.lu/fr/rss.xml | HTTP 404 |
| https://www.lessentiel.lu/rss | HTTP 404 |
| https://www.lessentiel.lu/rss/fr | HTTP 404 |
| https://www.london.gov.uk/media-centre/mayors-press-releases/rss | HTTP 404 |
| https://www.london.gov.uk/rss-feeds | HTTP 200 but no entries |
| https://www.london.gov.uk/rss.xml | stale: newest entry 819.0 days old |
| https://www.luxtimes.lu/feed | HTTP 403 |
| https://www.marianne.net/rss.xml | HTTP 403 |
| https://www.milanofinanza.it/rss | HTTP 404 |
| https://www.milanofinanza.it/rss/news | HTTP 404 |
| https://www.mit.gov.it/rss.xml | connection error |
| https://www.mobiliteit.lu/rss | HTTP 404 |
| https://www.money.it/rss | HTTP 404 |
| https://www.money.it/rss.xml | HTTP 404 |
| https://www.mta.info/rss.xml | HTTP 403 |
| https://www.nuovavenezia.it/feed | HTTP 404 |
| https://www.nuovavenezia.it/rss.xml | HTTP 404 |
| https://www.nyc.gov/apps/rss/mayor.rss | HTTP 490 |
| https://www.nyc.gov/office-of-the-mayor/news.rss | HTTP 200 but no entries |
| https://www.nyc.gov/site/office-of-the-mayor/news.rss | HTTP 490 |
| https://www.nydailynews.com/feed/ | HTTP 403 |
| https://www.oecd.org/en/rss.xml | HTTP 403 |
| https://www.oecd.org/newsroom/index.xml | HTTP 403 |
| https://www.ons.gov.uk/news/rss | HTTP 404 |
| https://www.paris.fr/actualites.rss | HTTP 200 but no entries |
| https://www.paris.fr/rss | HTTP 404 |
| https://www.parliament.uk/business/news/rss | HTTP 403 |
| https://www.pbc.gov.cn/en/3688110/3688172/rss.xml | HTTP 404 |
| https://www.politico.com/rss/politicopicks.xml | HTTP 403 |
| https://www.rainews.it/rss/economia | HTTP 404 |
| https://www.ratp.fr/rss | HTTP 403 |
| https://www.regione.veneto.it/rss | HTTP 403 |
| https://www.regione.veneto.it/web/guest/rss | HTTP 404 |
| https://www.skytg24.it/rss | HTTP 200 but no entries |
| https://www.spectator.co.uk/feed | HTTP 404 |
| https://www.startmag.it/feed/ | HTTP 403 |
| https://www.tageblatt.lu/feed | HTTP 404 |
| https://www.tageblatt.lu/feed/ | HTTP 404 |
| https://www.tageblatt.lu/rss | HTTP 404 |
| https://www.teleborsa.it/RSS/Notizie | HTTP 404 |
| https://www.teleborsa.it/rss | HTTP 200 but no entries |
| https://www.telegraph.co.uk/rss.xml | HTTP 402 |
| https://www.theguardian.com/business/tax/rss | HTTP 404 |
| https://www.theguardian.com/world/european-union/rss | HTTP 404 |
| https://www.theguardian.com/world/milan/rss | HTTP 404 |
| https://www.theguardian.com/world/newyork/rss | HTTP 404 |
| https://www.theguardian.com/world/rome/rss | HTTP 404 |
| https://www.thenationalnews.com/rss | HTTP 404 |
| https://www.trenitalia.com/rss | HTTP 404 |
| https://www.veneziaunica.it/rss | HTTP 403 |
| https://www.wallstreetitalia.com/feed/ | HTTP 403 |
| https://www.wsj.com/xml/rss/3_7031.xml | HTTP 403 |
| https://www3.nhk.or.jp/nhkworld/en/news/feeds/ | HTTP 404 |
| https://xml2.corriereobjects.it/rss/economia.xml | stale: newest entry 1002.2 days old |
| https://xml2.corriereobjects.it/rss/homepage.xml | stale: newest entry 898.3 days old |
| https://xml2.corriereobjects.it/rss/politica.xml | stale: newest entry 813.2 days old |

Publications from the brief with no working free feed found: Bloomberg and Reuters (no public RSS); Les Échos, Le Figaro, Le Point, La Stampa,
Il Post, Fanpage, Wall Street Italia, Crain's New York, NY Daily News, BLS, MTA, TfL, NYC.gov, Regione Veneto, Banque de France (refuse automated readers);
Paperjam, Delano, L'essentiel, Tageblatt, La Nuova di Venezia, Il Mattino di Padova, MF Milano Finanza, ItaliaOggi, Teleborsa, Banca d'Italia, Consob,
Agenzia delle Entrate, Chambre des Députés, INSEE (no endpoint found); Washington Post (HTTP 502 at test time).
