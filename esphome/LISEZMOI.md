# Airzone sur ESP32 (ESPHome)

Portage du pont `../airzone-sniffer/airzone_bridge.py` sur un **M5Stack ATOM Lite** avec le module **Tail485** (SKU T002).
L'ESP32 remplace le Raspberry Pi : même principe (second maître Modbus, il ne parle que dans les silences du
webserver), mêmes trames que l'appli, mais intégration native dans Home Assistant (pas de MQTT).

## Fichiers
| Fichier | Rôle |
|---|---|
| `airzone.yaml` | configuration ESPHome : bus, zones, capteurs, réglages |
| `modeles/zone.yaml` | une zone (thermostat + capteurs température et humidité), incluse une fois par zone ; dans un sous-dossier pour que Device Builder ne la prenne pas pour un appareil |
| `components/airzone/` | composant écrit pour ce projet : gestion du bus (C++) et thermostat des zones |
| `secrets.yaml.example` | à copier en `secrets.yaml` (Wi-Fi, clés) : ne jamais publier ce fichier |

## Le matériel
Le Tail485 se branche sur l'ATOM par deux connecteurs : sa prise USB-C mâle (alimentation 5 V de l'ATOM) et le
câble Grove (5 V, GND, TX = G26, RX = G32, déjà réglés dans `airzone.yaml`). Le sens émission/réception est
automatique (SP485EEN), il n'y a pas de broche de direction.

Bornier du Tail485 : `B`, `A`, `12V` (9 à 24 V), `GND`. Le 12V/GND alimente l'ensemble par un convertisseur
vers 5 V. Comme le Tail485 occupe la prise USB-C de l'ATOM, il n'y a **pas d'autre alimentation possible une fois
assemblé** : il faut du 9–24 V sur le bornier.

## Avant de flasher
1. **Mesurer la tension du bus** entre `+` et `−` du bornier `SHIELD A − B +` de l'Airzone, au multimètre, PAC en
   marche. Entre 9 et 24 V : `+` → `12V` et `−` → `GND` du Tail485. En dehors : ne pas brancher, il faudra une
   alimentation séparée (9–24 V, son `−` relié au `−` du bus).
2. Copier `secrets.yaml.example` en `secrets.yaml` et le remplir.

## Flasher
**La première fois, l'ATOM seul** (Tail485 retiré), en USB-C sur le PC ou le serveur HA. Ensuite, tout se fait
par le Wi-Fi (OTA), y compris le journal : l'ATOM ne sera plus jamais branché en USB une fois sur le bus, ce qui
évite de relier la masse du PC à celle de la PAC.

Avec l'add-on ESPHome de Home Assistant : copier le dossier `esphome/` dans `/config/esphome/` (les dossiers `modeles` et
`components` doivent être à côté de `airzone.yaml`), puis *Install*.

En ligne de commande : `esphome run airzone.yaml`.

## Mise en service, prudemment
1. **Lecture seule d'abord** : `writes_enabled: false` (par défaut). L'ESP32 ne fait que des lectures, comme le Pi
   le faisait au début. Un ordre donné dans HA est journalisé (« Écritures désactivées … non envoyée ») mais rien
   n'est envoyé.
2. Couper le disjoncteur, brancher A → `A`, B → `B` (jamais `+` ou `−` sur A/B), `+` → `12V`, `−` → `GND`.
   Installation de référence : A et B par un câble Dupont mâle-mâle chacun (une broche enfoncée dans J2 du webserver et tenue à la
   colle chaude, l'autre vissée dans le bornier du Tail485), `+` et `−` sur le
   bornier libre AZ2 de la centrale (12 V mesurés ; J2 n'a pas de `+`). Arrêter le pont du Pi (`sudo systemctl stop airzone-bridge`) : deux
   maîtres en plus du webserver, c'est un de trop.
3. Comparer HA et l'appli pendant un moment : températures, consignes, qualité de l'air, réglages.
   Le journal (`esphome logs airzone.yaml`) ne doit pas montrer d'« Échec bus » répétés.
4. Puis passer `writes_enabled: true`, réinstaller, et tester une zone peu utilisée avant le reste.

Au démarrage, le composant vérifie que son encodeur reproduit à l'octet près les trames capturées de l'appli.
Sinon, il désactive lui-même les écritures (« Auto-test de l'encodeur échoué »).

## Entités
- par zone : thermostat (arrêt, chaud, froid, ventilation, déshumidification ; consigne 15–30 °C par 0,5 ; état
  chauffe / inactif) et capteurs de température et d'humidité ;
- qualité de l'air : CO2, COV, PM2.5, PM10, humidité, température, pression, niveau (bon / moyen / mauvais),
  ventilateur du capteur ;
- réglages : 5 interrupteurs et 6 valeurs, aux plages et pas de l'appli ;
- mode général (sélecteur : stop, chaud, froid, ventilation, déshumidification) et connectivité du bus (diagnostic).

Le mode est commun à toute l'installation : choisir un autre mode sur un thermostat de zone change le mode général
(toutes les zones suivent), puis allume la zone, comme le font les programmations de l'appli.

## Différences avec le pont du Pi
- Les zones sont listées dans `airzone.yaml` (pas de détection automatique) : `zones:` et un `packages:` par zone.
- Les entités ont de nouveaux identifiants : l'historique des entités MQTT ne se raccroche pas tout seul.
  Supprimer l'appareil MQTT dans HA une fois l'ESP32 validé, et arrêter le service sur le Pi
  (`sudo systemctl disable --now airzone-bridge`).
