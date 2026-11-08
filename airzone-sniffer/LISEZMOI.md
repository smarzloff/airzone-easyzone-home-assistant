# Pilotage local Airzone → Home Assistant

Un Raspberry Pi, branché sur le **bus domotique** de la centrale Airzone, publie chaque zone dans
Home Assistant par MQTT. Il n'y a ni cloud, ni abonnement. Le webserver Airzone et l'appli officielle continuent de fonctionner à côté.

```
thermostats radio ──► centrale Airzone ◄══ bus domotique (Modbus RTU 19200 8E1) ══► webserver ──► cloud / appli
                       (AZPV8CB1IAQ)                ║
                                                    ╚══► clé RS-485 ── Pi (airzone_bridge.py) ── MQTT ──► Home Assistant
```

Le webserver interroge la centrale par rafales, séparées d'environ 0,8 s de silence. Le Pi est un **second maître** : il
attend un silence d'au moins 400 ms avant chaque requête, ce qui évite les collisions (toutes les 10 minutes, le
webserver fait une courte pause de 0,23 s en plein cycle : avec 200 ms, le Pi y émettait et entrait en collision).

## Matériel en place
- Centrale **AZPV8CB1IAQ** (Easyzone 25), passerelle Mitsubishi AZX8GTCMEL (CN105, bornier `IU`), webserver **AZX8WS1BWF**
  (carte AZxxxWSCLOUDR_3). Le webserver est enfiché dans le bornier 5 points `SHIELD A − B +` du bord gauche de la carte mère.
- Raspberry Pi 3B (Raspberry Pi OS Lite 64 bits), près de la centrale.
- Clé USB-RS485 JZK (CH340, `/dev/ttyUSB0`).
- Un broker MQTT (par exemple l'add-on Mosquitto de Home Assistant), avec un utilisateur dédié au pont.

## Câblage
**Toujours couper le disjoncteur avant de toucher à la centrale.**

Le Pi se branche sur **J2**, la rangée de 4 petits trous du webserver, côté gauche. Deux de ces trous sont reliés directement à
`A` et `B` du connecteur du bus, ce qui a été vérifié au multimètre. Aucun n'est relié au `+`.
- `A` de la clé sur le trou relié à `A`, et `B` sur le trou relié à `B`. Une inversion ne casse rien, mais on ne lit que du bruit.
- **Ne jamais brancher la clé sur `+` ni sur `−`**, et vérifier qu'aucun brin ne peut toucher ces deux trous.
- Pour retrouver A et B : disjoncteur coupé et webserver retiré, multimètre en mode continuité (position ▶| •)), bouton
  HOLD/SEL). Une pointe dans un trou de J2, l'autre sur chaque broche du connecteur du webserver. L'ordre des broches est
  celui de la sérigraphie de la carte mère : SHIELD, A, −, B, +.
- Montage retenu, sans souder sur la carte (disjoncteur coupé) : **un câble Dupont mâle-mâle par fil**.
  1. enfoncer une broche mâle directement dans le trou de J2 relié à `A` (puis `B`), en deux couleurs ;
  2. l'immobiliser avec une goutte de colle chaude sur son plastique, jamais dans le trou, et vérifier que rien ne
     dépasse sous le webserver ni ne touche la carte mère ;
  3. visser l'autre broche mâle dans le bornier de la clé (ou du Tail485), enfoncée jusqu'au plastique, puis tirer
     légèrement pour vérifier qu'elle tient. Noter ici les couleurs utilisées pour A et B ;
  4. éviter les rallonges Dupont bout à bout : chaque raccord est un faux contact possible ;
  5. poser le capot sans forcer sur les fils, puis tester (service arrêté) : `python3 domo.py lire 6 --send`.

  Essayé d'abord : une barrette de 4 broches mâles **coudées à 90°** (pas de 2,54 mm), petites pattes forcées dans J2
  et tenue à la colle chaude, avec des Dupont femelles dessus. Elle n'a pas tenu et a mis le webserver en erreur :
  je ne suis pas très doué pour ce genre de montage :) . À réserver à qui sait souder proprement les 4 pattes sur la
  carte (en coupant ou isolant les pattes de `+` et `−`). La pince à pogo pins ne passe pas, faute de place contre la paroi.
- Le Pi doit être alimenté par un bloc secteur 5 V 2,5 A sur une prise fixe.

## Installation du pont
Sur le Pi :
```bash
sudo apt install -y python3-serial python3-paho-mqtt
sudo usermod -aG dialout $USER          # puis se reconnecter
```
Depuis le PC :
```bash
scp -r airzone-sniffer/*.py airzone-sniffer/deploy pi@<ip-du-pi>:airzone-sniffer/
```
Sur le Pi :
```bash
cd ~/airzone-sniffer
cp deploy/bridge.ini.example bridge.ini && chmod 600 bridge.ini
nano bridge.ini                          # adresse du broker, utilisateur et mot de passe MQTT
python3 airzone_bridge.py --config bridge.ini     # essai à la main, arrêt avec Ctrl+C
sudo cp deploy/airzone-bridge.service /etc/systemd/system/
sudo systemctl enable --now airzone-bridge
```
Mise à jour : recopier les `.py`, puis `sudo systemctl restart airzone-bridge`.

Pense à garder une copie de `bridge.ini` et du dossier sur le PC : si la carte SD lâche, tout se réinstalle en 10 minutes.

## Dans Home Assistant
Les entités apparaissent automatiquement (Paramètres → Appareils → MQTT) : un appareil par zone, avec
- un **thermostat** : marche/arrêt, consigne de 15 à 30 °C par pas de 0,5, température, humidité, et état (chauffe, inactif ou arrêt) ;
- un **capteur de température** et un **capteur d'humidité**, pour les historiques et les graphiques.

S'il est présent, le capteur de qualité de l'air apparaît comme un appareil à part :
- des capteurs : CO2, COV totaux, PM2.5, PM10, humidité, température et pression ;
- un **niveau de qualité** (bon, moyen ou mauvais, comme la couleur de la LED) et l'état du **ventilateur du capteur** ;
- dans sa partie configuration, 5 interrupteurs (LED, ventilation cyclique, free-cooling, mode nuit, priorité QAI)
  et 6 réglages chiffrés : heures de début et de fin du mode nuit, intervalle (10 à 60 min) et durée minimale
  (5 à 55 min) de la ventilation cyclique, température minimale (18 à 30 °C) et différentiel (0 à 10) du
  free-cooling. Le pont n'envoie jamais une valeur hors des plages et des pas de l'appli.

Un appareil « Airzone » porte le **mode général** (chaud, froid, ventilation, déshumidification ou stop), en lecture seule.

Les zones sont détectées automatiquement (celles qui ont un nom). La centrale stocke les noms sur
11 caractères : on peut les renommer dans HA.

**Limite :** le mode général (chaud, froid…) se règle sur le thermostat filaire ou dans l'appli. Quand l'appli le
change, aucune écriture n'apparaît sur ce bus : le pont peut le lire, pas le changer. Une zone ne propose donc dans HA
que « off » et le mode en cours, et l'allumer ne change pas ce mode. C'est pareil pour l'eco-adapt.

Le pont relit toutes les zones toutes les 30 s (`interval` dans `bridge.ini`). Un ordre donné dans HA part tout de
suite, puis la zone est relue.

Topics MQTT :
- zones : `airzone/zone/N/{mode,setpoint,temperature,humidity}`, commandes sur `…/mode/set` et `…/setpoint/set` ;
- qualité de l'air : `airzone/iaq/{co2,tvoc,pm25,pm10,humidity,temperature,pressure,level,fan}` ;
- réglages : `airzone/config/<clé>`, commandes sur `…/set` (`ON`/`OFF` pour les interrupteurs, un nombre pour les autres) ;
- mode général : `airzone/system/mode` ;
- disponibilité : `airzone/status` (`online` ou `offline`).

## Dépannage
```bash
systemctl status airzone-bridge
journalctl -u airzone-bridge -f
```
| Symptôme | Cause probable |
|---|---|
| `Pas de trafic suivi d'un silence en 10 s` | webserver débranché ou éteint (le pont a besoin de son trafic pour trouver les silences), clé débranchée, ou fils A/B qui ont bougé |
| Entités « indisponible » dans HA | 3 échecs de suite sur le bus (voir le journal), ou pont arrêté |
| `connexion MQTT refusée` | identifiant ou mot de passe faux dans `bridge.ini` |
| `zone N absente (exception Modbus 11)` | normal : la zone n'existe pas |
| `Permission denied: /dev/ttyUSB0` | utilisateur absent du groupe `dialout`, ou autre programme qui utilise déjà la clé |

Test manuel, à faire **service arrêté** (`sudo systemctl stop airzone-bridge`), car le port n'accepte qu'un programme à la fois :
```bash
python3 domo.py lire 6 --send                     # lecture seule
python3 domo.py marche 6 --send                   # ou arret
python3 domo.py consigne 6 --valeur 16 --send     # relit la zone après chaque écriture
```
Sans `--send`, `domo.py` affiche la trame sans rien envoyer.

## Protocole du bus domotique
Modbus RTU, 19200 bauds, 8 bits, parité paire, 1 stop. La centrale est l'**esclave 1**.

Zone *n* : registres à partir de *n* × 256 (zone 6 → `0x0600`). Valeurs relevées :

| Relatif | Contenu |
|---|---|
| +0 | état : bit `0x0004` = marche ; bits 8-11 = mode (0 stop, 1 froid, 3 ventilation, 5 chaud, 6 déshumidification) |
| +1 / +2 | consigne minimale en froid / maximale en chaud, en °C × 10 (fixées par l'eco-adapt : off 18/30, A 24/22, A+ 25/20,5, A++ 26/19) |
| +3 | consigne, en °C × 10. La centrale en garde une par mode et la remplace quand le mode général change. |
| +8 (et +10) | température, en °C × 10 |
| +9 | bit `0x0080` = la zone demande (chauffe ou froid) ; à 0 quand la consigne est atteinte. Le pont l'affiche dans HA (« Chauffe » / « Inactif »). |
| +14 à +29 | nom de la zone (ASCII / Latin-1) |
| +0x1F | humidité, en % |
| +0x39 | niveau de qualité de l'air : 6 bon (LED verte), 10 moyen (orange), 14 mauvais (rouge). Copie dans l'octet bas de `0x0090` (5, 6, 7). |
| +0x3B / +0x3C | PM2.5 / PM10 (copies du capteur) |

Mode général : registre `0x0000`, octet bas (0 stop, 1 froid, 2 chaud, 3 ventilation, 6 déshumidification ;
en chaud le registre vaut `0x0102`). Relevé le 06/11/2026.

Commandes de l'appli, reproduites telles quelles par le pont :
- marche : fonction `0x16` (écriture par masque) sur *zone*+0, masque ET `0xFFFB`, OU `0x0004`. Pour l'arrêt, OU `0x0000`.
  La centrale acquitte par `01 16 RR RR 00 00 FF FB`. C'est inhabituel, mais c'est sa réponse normale.
- consigne : fonction `06` sur *zone*+3. La réponse est l'écho de la requête.

Capteur de qualité de l'air : bloc `0x2100` (33 registres), identifié le 06/11/2026 en comparant avec l'appli.
Le pont le publie dans HA comme un appareil « Qualité de l'air ».

| Registre | Mesure |
|---|---|
| `0x2105` / `0x2106` | PM2.5 / PM10, en µg/m³ (recopiés dans chaque zone, aux registres +0x3B et +0x3C) |
| `0x2107` | CO2, en ppm |
| `0x2108` | COV totaux (TVOC), en ppb |
| `0x2109` | humidité, en % |
| `0x210A` | température, en °C × 10 (probable, pas encore vérifiée avec l'appli) |
| `0x210B` | pression, en hPa |
| `0x2103`, bit 0 | ventilateur du capteur en marche (démarré par la ventilation cyclique) |
| `0x2119` / `0x211A` / `0x211D` | sous-scores probables CO2 / COV / humidité, de 3 (mauvais) à environ 800 (bon) |

L'indice chiffré de l'appli (0 à 100) n'apparaît pas sur le bus : il semble calculé par le webserver ou le cloud.

Réglages de l'appli (relevés du 06/11/2026), écrits par masque (fonction 0x16) comme le fait l'appli.
Interrupteurs :

| Réglage | Registre | Bit (1 = activé) |
|---|---|---|
| LED du capteur | `0x2101` | `0x0100` |
| Ventilation cyclique | `0x2101` | `0x1000` |
| Free-cooling | `0x2101` | `0x4000` |
| Mode nuit | `0x2121` | `0x0001` |
| Priorité QAI | `0x0038` | `0x2000` |

Valeurs (masque = bits du champ ; la valeur est décalée au premier bit du masque) :

| Réglage | Registre | Masque | Exemple de l'appli |
|---|---|---|---|
| Mode nuit : heure de début | `0x2121` | `0x007C` (bits 2-6) | 2 h → `01 16 21 21 FF 83 00 08` |
| Mode nuit : heure de fin | `0x2121` | `0x0F80` (bits 7-11) | 10 h → `01 16 21 21 F0 7F 05 00` |
| Ventilation cyclique : intervalle, min | `0x211F` | `0x00FF` | 50 → `01 16 21 1F FF 00 00 32` |
| Ventilation cyclique : durée minimale, min | `0x211F` | `0xFF00` | 30 → `01 16 21 1F 00 FF 1E 00` |
| Free-cooling : température minimale, °C | `0x2120` | `0x00FF` | 25 → `01 16 21 20 FF 00 00 19` |
| Free-cooling : différentiel | `0x2120` | `0xFF00` | 7 → `01 16 21 20 00 FF 07 00` |

Les jours de la ventilation cyclique et les mois du free-cooling ne passent pas sur le bus : **le webserver tient le
calendrier**. Toutes les 5 minutes, il remet à 1 les bits `0x2000` (« ventilation cyclique autorisée aujourd'hui ») et
`0x8000` (« free-cooling autorisé ce mois-ci ») de `0x2101`, ou les remet à 0 si le jour ou le mois est exclu. Il
faudrait le reproduire si le webserver est retiré. Le ventilateur ne se pilote pas directement depuis l'appli.

Le webserver lit aussi `0x0000` (80 registres), `0x0050` (80) et `0x2900` à `0x3000` (12 chacun, un bloc par
thermostat ?), encore largement inexplorés.
Toutes les 45 s environ, il remet à 1 le bit `0x0010` du registre `0x0001`, que la centrale efface entre-temps. C'est
probablement un signal de présence de la domotique. Si un jour le Pi **remplace** le webserver, il faudra sans doute le
reproduire. C'est peut-être la cause de l'« erreur 12 » signalée sur le forum HACF.

La doc Modbus Airzone de la génération AZ6 (`MI_AZ6_MODBUS`, sur le site d'Airzone) suit la même logique de zones, mais pas la même position des registres.

## Annexe : bus des thermostats (AZ1 / AZ2 / AZ3)
Les trois borniers du bas sont le même bus, en parallèle. Le thermostat filaire est câblé sur AZ3. Ce bus ne transporte
que le thermostat filaire (adresse `0x32`) et le mode général : les ordres des zones radio n'y passent pas.

- 9600 bauds, 8N1. Trame : `AA | adresse | longueur | type | données | CRC16-Modbus` (petit-boutiste, calculé sur tout sauf `AA`).
- Type 02 (thermostat → centrale, toutes les 75 ms) : température en °C × 10 sur les octets 4-5 (petit-boutiste), humidité sur l'octet 6.
- Type 03 = commande du thermostat : `AA 32 08 03 DRAPEAUX CONS MODE MARCHE 01 CRC`.
  - consigne en °C = 10 + CONS / 10 ;
  - MODE : 01 stop, 02 ventilation, 04 froid, 20 chaud, 40 déshumidification ;
  - DRAPEAUX : 01 marche/arrêt, 21 consigne, 30 mode.
- La centrale répond par un type 12 (acquittement), puis un type 0d (état). Les types 53/54 donnent la demande machine (04 marche, 05 arrêt).

Outils : `capture.py scan|capture` (écoute passive), `decode.py` (lecture d'une capture), `inject.py`.
`inject.py` envoie une seule trame 03 : il n'est **pas encore testé sur le bus réel**.

## Fichiers
| Fichier | Rôle |
|---|---|
| `airzone_bridge.py` | le pont MQTT ↔ centrale, lancé en service |
| `domo.py` | une requête Modbus à la main (`lire`, `marche`, `arret`, `consigne`, `registres`), simulation par défaut |
| `capture.py` | écoute passive : `scan` (recherche de la vitesse), `capture` (enregistrement JSONL avec marqueurs) |
| `modbus.py` | analyse d'une capture du bus domotique (lectures, écritures, registres qui changent) |
| `decode.py`, `analyze.py`, `inject.py` | analyse et test du bus des thermostats |
| `deploy/bridge.ini.example`, `deploy/airzone-bridge.service` | configuration et service systemd |
