# Airzone Easyzone dans Home Assistant, en local, à côté du webserver officiel

Guide communautaire pour piloter une centrale **Airzone Easyzone** depuis Home Assistant, **sans cloud ni
abonnement**, en se branchant sur son **bus domotique RS-485 (Modbus RTU)**. Le webserver Airzone et l'appli
officielle **continuent de fonctionner** : notre montage ne les remplace pas, il parle dans leurs silences.

Deux variantes du même principe :

| Variante | Matériel | Intégration HA | État |
|---|---|---|---|
| **ESP32** (recommandée) | M5Stack ATOM Lite + Tail485 | ESPHome (native, sans MQTT) | flashé, mise en service sur le bus en cours |
| **Raspberry Pi** | Pi + clé USB-RS485 | pont Python → MQTT (découverte automatique) | en service, validé en lecture et en écriture |

## Contenu du dépôt
- `esphome/` : la configuration ESPHome et le composant `airzone` (C++), avec son `LISEZMOI.md` pas à pas ;
- `airzone-sniffer/` : le pont Python pour Raspberry Pi et les outils d'écoute et d'analyse du bus (les captures
  brutes qui ont servi au décodage ne sont pas publiées) ;
- ce `README.md` : le principe, le matériel, le raccordement et la carte des registres.

## Le principe
```
thermostats ──► centrale Airzone ◄══ bus domotique (Modbus RTU 19200 8E1) ══► webserver ──► cloud / appli
                                              ║
                                              ╚══► ATOM + Tail485 (ESPHome) ──► Home Assistant
```
- Sur ce bus, la centrale est l'**esclave 1** et le webserver est **maître** : il l'interroge par rafales séparées
  d'environ 0,8 s de silence.
- Notre montage est un **second maître**. Il n'émet qu'après **au moins 400 ms de silence**, et jamais s'il n'entend pas
  le webserver. Il ne gêne donc pas le webserver.
  Pourquoi 400 ms : toutes les 10 minutes, le webserver fait une courte pause de 0,23 s au milieu d'un cycle. Avec un
  seuil de 200 ms, on émettait dedans et on entrait en collision avec lui.
- Les commandes envoyées sont **les mêmes trames, octet pour octet, que celles de l'appli**. On les a capturées sur le
  bus, et le firmware les revérifie à chaque démarrage (auto-test de l'encodeur).

## Matériel testé
- Centrale **AZPV8CB1IAQ** (Easyzone, avec capteur de qualité de l'air) et webserver **AZX8WS1BWF**, enfiché dans le
  bornier 5 points `SHIELD A − B +` de la carte mère. La passerelle de PAC (ici Mitsubishi, AZX8GTCMEL) n'intervient pas.
- **M5Stack ATOM Lite** (ESP32-PICO) + **M5Stack Tail485** (SKU T002) :
  - transceiver SP485EEN à **direction automatique** : aucune broche de direction ;
  - **convertisseur 9–24 V → 5 V intégré** : l'ATOM est alimenté par le bus lui-même.
- Quelques câbles Dupont **mâle-mâle** (un par fil) et de la colle chaude.

> Ce montage a été validé sur **une** installation. D'autres centrales Airzone (AZ6, Innobus, CE6…) utilisent la même
> logique de zones, mais **pas forcément les mêmes registres** : vérifiez par une écoute avant d'écrire quoi que ce soit.

## Raccordement
**Toujours couper le disjoncteur de la PAC avant de toucher à la centrale.**

Ici, la référence est **le nom des bornes**, pas la couleur des fils.

| Bus Airzone | Tail485 | Où le prendre |
|---|---|---|
| `A` | `A` | trou de **J2** du webserver relié à `A` (broche Dupont mâle collée, voir ci-dessous) |
| `B` | `B` | trou de **J2** du webserver relié à `B` |
| `+` (12 V) | `12V` | bornier **AZ2** de la centrale, borne `+` |
| `−` | `GND` | bornier **AZ2** de la centrale, borne `−` (pas `SHIELD`) |

- **Jamais `+` ni `−` sur A ou B.**
- **Mesurez d'abord la tension** entre `+` et `−`, au multimètre en tension continue (V⎓, calibre 200). Ici : 12 V. Il
  faut **entre 9 et 24 V** pour alimenter le Tail485 directement. Sinon, prévoyez une alimentation séparée dont le
  `−` est relié au `−` du bus.
- **J2** est la rangée de 4 petits trous sur le côté du webserver. Seuls deux d'entre eux ont été identifiés, au
  multimètre en continuité : A et B. Aucun n'est relié au `+`. Les bornes AZ1, AZ2 et AZ3 sont en parallèle ;
  AZ2 était libre.
- **Piquage sur J2** : une broche Dupont **mâle** enfoncée directement dans le trou, maintenue par une goutte de colle
  chaude sur son plastique (jamais dans le trou). L'autre bout du même câble, mâle aussi, est vissé dans le bornier du
  Tail485, broche enfoncée jusqu'au plastique. Un seul câble par fil : pas de rallonges Dupont bout à bout, chaque
  raccord est un faux contact possible.
  Une barrette de broches coudées collée dans J2 a été essayée d'abord : elle n'a pas tenu et a mis le webserver en
  erreur (je ne suis pas très doué pour ce genre de montage :) ). Réservée à qui sait souder
  proprement sur la carte.
- Le `GND` du Tail485 relié au `−` du bus sert aussi de **référence au signal RS-485**. Ne l'oubliez pas.
- **Côté Tail485**, c'est un bornier à vis débrochable, dans l'ordre `B A 12V GND`. Câblez-le seul, contrôlez au
  multimètre (≈ 12 V entre `12V` et `GND`, quelques volts seulement entre `A` et `GND`), puis enfichez-le.
- **Le Tail485 occupe la prise USB-C de l'ATOM.** Une fois assemblé, l'ATOM ne s'alimente plus que par le bornier.
  Il n'est plus jamais branché sur un PC, ce qui évite de relier la masse du PC à celle de la PAC.

### À propos des couleurs de fils
Les couleurs utilisées ici sont notées dans l'installation, **ce n'est pas une norme**. Étiquetez vos fils (A, B, +, −)
avant de visser.

## Installation (ESPHome)
Le détail est dans `esphome/LISEZMOI.md`. En résumé :
1. Copiez `airzone.yaml`, le dossier `modeles/` et le dossier `components/` dans `/config/esphome/` de Home
   Assistant. `zone.yaml` est rangé dans `modeles/` pour que Device Builder ne le prenne pas pour un appareil.
2. Ajoutez `api_key` et `ota_password` dans le `secrets.yaml` d'ESPHome. Ce fichier est **commun** à tous vos appareils :
   ne l'écrasez pas.
3. Adaptez la liste `zones:` et les lignes `packages:` à vos zones (numéro et nom).
4. **Premier flash, ATOM seul**, sans le Tail485 :
   - en USB, avec l'**image d'usine** de Device Builder et `web.esphome.io` (Chrome ou Edge). Elle met aussi à jour
     le chargeur de démarrage ;
   - ou en OTA, si l'ATOM tourne déjà sous ESPHome. Gardez alors le même framework (Arduino ici) et l'ancien mot de
     passe OTA, et ajoutez temporairement `use_address:` si le nom de l'appareil a changé.
5. Câblez (disjoncteur coupé), remettez le courant, puis suivez le journal par le Wi-Fi.

## Mise en service, prudemment
1. **Lecture seule d'abord** : `writes_enabled: false`, c'est le réglage par défaut. Un ordre donné dans HA est seulement
   journalisé (« Écritures désactivées … non envoyée »).
2. Avec la variante Pi en place, **arrêtez son service** (`sudo systemctl stop airzone-bridge`). Deux maîtres en plus du
   webserver, c'est un de trop.
3. Comparez HA et l'appli pendant un moment : températures, consignes, qualité de l'air, réglages. « Bus Airzone »
   doit être **connecté**, et le journal ne doit pas montrer d'« Échec bus » répétés.
4. Passez ensuite `writes_enabled: true`, réinstallez, et testez d'abord une zone peu utilisée.

Garde-fous du firmware :
- il n'émet jamais sans trafic du webserver, ni avant 400 ms de silence ;
- il écarte les valeurs incohérentes ;
- il n'envoie jamais une valeur hors des plages et des pas de l'appli ;
- il coupe lui-même les écritures si l'auto-test de l'encodeur échoue ;
- ses interrupteurs ne font **rien au démarrage** (`restore_mode: DISABLED`). Sans ce réglage, chaque redémarrage
  aurait éteint les 5 réglages de l'Airzone.

## Ce qu'on obtient dans Home Assistant
- **Par zone** :
  - un thermostat : arrêt, chaud, froid, ventilation ou déshumidification (choisir un autre mode que le mode en
    cours change le **mode général**, donc toutes les zones) ; consigne de 15 à 30 °C par 0,5 ; état chauffe ou
    inactif ;
  - des capteurs de température et d'humidité.
- **Qualité de l'air** :
  - mesures : CO2, COV totaux, PM2.5, PM10, humidité, température, pression ;
  - niveau bon / moyen / mauvais, comme la couleur de la LED ;
  - ventilateur du capteur.
- **Réglages**, aux plages et pas de l'appli :
  - interrupteurs : LED du capteur, ventilation cyclique, free-cooling, mode nuit, priorité QAI ;
  - valeurs : heures de début et de fin du mode nuit, intervalle (10–60 min) et durée minimale (5–55 min) de la
    ventilation cyclique, température minimale (18–30 °C) et différentiel (0–10) du free-cooling.
- **Mode général** : un sélecteur stop, chaud, froid, ventilation, déshumidification ; et la connectivité du bus.

**Limites connues**
- L'**eco-adapt** ne se change pas par ce bus : on lit ses effets (consignes minimale et maximale), sans pouvoir
  l'écrire.
- Les **programmations** de l'appli ne sont pas stockées dans la centrale : le webserver envoie l'ordre à l'heure
  prévue. Les automatisations de Home Assistant font la même chose.
- L'**indice chiffré** de qualité de l'air de l'appli (0–100) n'est pas sur le bus. Il est probablement calculé par
  le webserver ou le cloud.
- Le **calendrier** (jours de la ventilation cyclique, mois du free-cooling) est tenu par le webserver. Si on le retirait,
  il faudrait le reproduire.

## Carte des registres (bus domotique)
Modbus RTU, **19200 bauds, 8 bits, parité paire, 1 stop**, esclave 1. Zone *n* : registres à partir de *n* × 256
(zone 6 → `0x0600`).

| Zone + | Contenu |
|---|---|
| +0 | état : bit `0x0004` = marche ; bits 8–11 = mode (0 stop, 1 froid, 3 ventilation, 5 chaud, 6 déshumidification) |
| +1 / +2 | consigne minimale en froid / maximale en chaud, en °C × 10. Elles sont fixées par l'eco-adapt : off 18/30, A 24/22, A+ 25/20,5, A++ 26/19 |
| +3 | consigne en °C × 10. La centrale en garde une par mode. |
| +8 | température en °C × 10 |
| +9 | bit `0x0080` = la zone demande (chauffe ou froid), à 0 une fois la consigne atteinte |
| +14 à +29 | nom de la zone (ASCII, 11 caractères) |
| +0x1F | humidité en % |
| +0x39 | niveau de qualité de l'air : 6 bon, 10 moyen, 14 mauvais |

Mode général : registre `0x0000`, octet bas (0 stop, 1 froid, 2 chaud, 3 ventilation, 6 déshumidification).

Capteur de qualité de l'air, bloc `0x2100` :

| Registre | Mesure |
|---|---|
| `0x2105` / `0x2106` | PM2.5 / PM10, µg/m³ |
| `0x2107` | CO2, ppm |
| `0x2108` | COV totaux, ppb |
| `0x2109` | humidité, % |
| `0x210A` | température, °C × 10 |
| `0x210B` | pression, hPa |
| `0x2103` bit 0 | ventilateur du capteur en marche |

Commandes de l'appli, reproduites telles quelles :
- **Marche / arrêt** : fonction `0x16` (écriture par masque) sur *zone*+0, masque ET `0xFFFB`, OU `0x0004` (marche) ou
  `0x0000` (arrêt). L'acquittement `01 16 RR RR 00 00 FF FB` est inhabituel, mais c'est la réponse normale de la
  centrale.
- **Consigne** : fonction `0x06` sur *zone*+3. La réponse est l'écho de la requête.
- **Mode** : fonction `0x16` sur les bits 8–11 de *zone*+0, masque ET `0xF0FF`, OU code × 256. Codes écrits :
  0 stop, 3 ventilation, 5 chaud, 6 déshumidification, 9 froid (relu 1). Écrit sur **n'importe quelle zone**, il
  change le mode général, donc toutes les zones ; chacune reprend sa consigne mémorisée pour ce mode. L'appli l'écrit
  seul (changement depuis la zone principale) ; ses programmations envoient mode, marche puis consigne, répétés toutes
  les 12 s pendant la minute de déclenchement (captures domo6 et domo7).
- **Réglages** : fonction `0x16`, un bit ou un champ de bits chacun.

| Réglage | Registre | Masque |
|---|---|---|
| LED du capteur | `0x2101` | `0x0100` |
| Ventilation cyclique | `0x2101` | `0x1000` |
| Free-cooling | `0x2101` | `0x4000` |
| Mode nuit | `0x2121` | `0x0001` |
| Priorité QAI | `0x0038` | `0x2000` |
| Mode nuit : heure de début / de fin | `0x2121` | `0x007C` / `0x0F80` |
| Ventilation cyclique : intervalle / durée minimale (min) | `0x211F` | `0x00FF` / `0xFF00` |
| Free-cooling : température minimale / différentiel (°C) | `0x2120` | `0x00FF` / `0xFF00` |

## Dépannage
| Symptôme | Cause probable |
|---|---|
| `Pas de trafic du webserver : rien n'est envoyé` | Tail485 non alimenté, fils A/B débranchés ou inversés, webserver absent. Le montage a besoin du trafic du webserver pour trouver les silences. |
| « Bus Airzone » déconnecté, capteurs « inconnu » | plusieurs échecs de suite sur le bus (voir le journal) |
| `Écritures désactivées … non envoyée` | normal tant que `writes_enabled: false` |
| Noms d'entités avec `├®` à la place de `é` | fichier YAML abîmé lors de la copie (passage par une console Windows) : retaper les accents dans l'éditeur de HA |
| `macro "bit" passed 2 arguments` à la compilation | ancienne version du composant avec le framework Arduino : reprendre la version actuelle |

## Confidentialité
Ce guide ne contient aucune adresse IP, aucun SSID, aucune adresse MAC, aucun identifiant, mot de passe ni code PIN.
Les noms de zones des exemples sont fictifs. Les captures brutes du bus ne sont pas publiées.
Avant de publier vos propres fichiers, vérifiez qu'ils ne contiennent pas vos données. **Ne publiez jamais
`secrets.yaml`** ni `bridge.ini`, et relisez vos captures, qui peuvent contenir les noms de vos zones.

## Sources
- Airzone : documentation Modbus de la génération AZ6 (`MI_AZ6_MODBUS`), même logique de zones mais registres placés
  différemment ;
- M5Stack : pages produit ATOM Lite et Tail485 (brochage : TX G26, RX G32) ;
- ESPHome : documentation des composants `uart`, `climate`, `template` et `external_components` ;
- le guide communautaire « Airzone CE6 dans Home Assistant avec un Elfin EW11 » (akunia/airzone-ce6-ew11a-home-assistant),
  dont ce document reprend la présentation ;
- les discussions Airzone du forum HACF.

## Avertissement
Projet personnel, non affilié à Airzone. Toute intervention sur la centrale se fait disjoncteur coupé, à vos
risques. Écouter le bus est sans danger. Écrire dessus ne l'est que si les trames sont celles de **votre** installation,
d'où la lecture seule par défaut.
