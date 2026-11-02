# MeshCore ↔ Domoticz Bridge

![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![MeshCore 1.18+](https://img.shields.io/badge/MeshCore-1.18+-green.svg)
![Domoticz](https://img.shields.io/badge/Domoticz-compatible-blueviolet.svg)

_Passez au [README en anglais](README.md) pour la version internationale._

Un pont Python léger qui interroge périodiquement la télémétrie d'un répéteur **MeshCore** distant (tension batterie + température) via une radio companion MeshCore, et publie les valeurs vers **Domoticz** par MQTT — pour que Domoticz les journalise et les trace comme n'importe quelle autre sonde.

```
┌──────────────────────────┐       LoRa mesh       ┌──────────────────────────┐
│  Répéteur distant        │  ◄──────────────────►  │  Companion (ex. Heltec   │
│  (ex. Faketec v3 +       │   req_telemetry        │  v4) en WiFi, IP fixée   │
│   pack 18650 sur toit)   │   ◄──────────────────► │  par réservation DHCP    │
│  - tension batterie      │   JSON (Cayenne LPP)   │                          │
│  - température           │                        └──────────┬───────────────┘
└──────────────────────────┘                                   │ TCP:5000
                                                               │
                                              ┌────────────────▼────────────────┐
                                              │  Raspberry Pi (ou tout Linux)   │
                                              │  ┌──────────────────────────┐   │
                                              │  │ meshcli req_telemetry    │   │
                                              │  │ (toutes les 3h, timer    │   │
                                              │  │  systemd)                │   │
                                              │  └────────────┬─────────────┘   │
                                              │               │ JSON            │
                                              │  ┌────────────▼─────────────┐   │
                                              │  │ meshcore_to_domoticz.py  │   │
                                              │  └────────────┬─────────────┘   │
                                              │               │ MQTT            │
                                              │  ┌────────────▼─────────────┐   │
                                              │  │ Mosquitto + Domoticz     │   │
                                              │  │ Courbes tension + temp   │   │
                                              │  └──────────────────────────┘   │
                                              └─────────────────────────────────┘
```

## Pourquoi ?

MeshCore est un firmware LoRa mesh basse consommation avec une architecture companion/répéteur propre. Les nœuds répéteurs (souvent des cartes nRF52840 Pro Micro comme le Faketec v3) exposent déjà la tension batterie et la température interne du MCU via la commande `req_telemetry` de MeshCore — aucun capteur supplémentaire à ajouter.

**Home Assistant** dispose déjà d'une intégration officielle MeshCore (`meshcore-ha`), mais **Domoticz** n'en a pas. Ce projet remplit ce vide avec une approche minimaliste : un script Python, un timer systemd, pas de daemon.

## Fonctionnalités

- **Léger** : un seul script Python lancé toutes les 3h par un timer systemd — pas de daemon always-on, pas de charge supplémentaire sur le Pi.
- **Polling respectueux de la batterie** : le répéteur n'émet que lorsqu'on l'interroge, au lieu de diffuser sa télémétrie en continu.
- **Aucun matériel supplémentaire** : utilise l'ADC batterie natif du répéteur + la température interne du MCU (pas de BME280/DS18B20 à câbler).
- **WiFi ou USB** : le companion peut être joint en TCP (firmware MeshCore v1.18+ BLE+WiFi recommandé — garde le BLE pour l'app mobile pendant que le script poll en TCP), ou en série USB comme fallback.
- **Parsing robuste** : gère le format Cayenne LPP renvoyé par meshcli ; tolère la réordonnancement et les futurs types de capteurs inconnus.
- **Adapté à la mobilité** : quand tu emportes le companion en déplacement (hors WiFi maison), le script logge juste une erreur de connexion et réessaie au prochain cycle 3h — Domoticz conserve la dernière valeur, pas de casser le graphique.

## Prérequis

### Côté répéteur

- Un répéteur MeshCore (toute carte supportée : Faketec v3, Heltec v3/v4, RAK WisBlock, etc.)
- Un firmware assez récent pour répondre à `req_telemetry` avec un payload Cayenne LPP contenant au moins `voltage` et `temperature` (la plupart des builds répéteur sur nRF52 le font).

### Côté companion

- Une radio companion MeshCore (typiquement un Heltec v3 ou v4).
- **Recommandé** : firmware MeshCore **v1.18.0+**, build "BLE+WiFi". Celui-ci expose :
  - **BLE** pour l'app mobile officielle MeshCore (quand tu emportes le companion sur le terrain)
  - **TCP sur le port 5000** pour ce script en WiFi
- Mettre une **réservation DHCP** sur ta box pour que l'IP du companion ne change pas.

### Côté hôte Domoticz (Raspberry Pi ou tout Linux)

- Domoticz installé et fonctionnel
- Mosquitto (ou tout broker MQTT) installé à côté de Domoticz
- Python 3.10+ (le paquet `meshcore-cli` le requiert)
- Les paquets Python `meshcore-cli` et `paho-mqtt`

> **Note pour les vieux OS (Raspberry Pi OS Buster, Debian 10, etc.)** : le Python système est 3.7, trop vieux pour `meshcore-cli`. Voir **[Dépannage](#dépannage)** ci-dessous pour une install propre de Python 3.11 via `pyenv` à côté du Python système — pas de reflashage requis.

## Installation

### 1. Installer Mosquitto (si pas déjà présent)

```bash
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

### 2. Installer meshcli

La méthode la plus propre est via `pipx` (install isolé, ne pollue pas le Python système) :

```bash
sudo apt install -y pipx
pipx ensurepath
# fermer et rouvrir le terminal, puis :
pipx install meshcore-cli
```

Ou via un venv (préféré si ton Python système est vieux et tu as besoin de pyenv — voir dépannage) :

```bash
python3 -m venv ~/meshcore-venv
source ~/meshcore-venv/bin/activate
pip install meshcore-cli paho-mqtt
```

### 3. Flasher le companion avec le firmware BLE+WiFi

Si ton companion est un Heltec v4, utilise le flasher web officiel sur <https://flasher.meshcore.co.uk> et sélectionne la variante **BLE+WiFi** pour le Heltec v4 (requiert firmware MeshCore **v1.18.0+**).

Configure ensuite les identifiants WiFi sur le companion via l'app mobile MeshCore (Settings → WiFi Settings) ou via CLI :

```bash
meshcli -j -t <ip_companion> -p 5000 set wifi.ssid "TON_SSID"
meshcli -j -t <ip_companion> -p 5000 set wifi.pwd "TON_MOT_DE_PASSE"
```

Mets une **réservation DHCP** pour le companion sur ta box afin que son IP reste stable.

### 4. Trouver le nom du contact du répéteur

Liste tous les contacts connus du companion :

```bash
meshcli -j -t 192.168.1.XX -p 5000 contacts
```

Dans la sortie JSON, trouve l'entrée correspondant à ton répéteur et copie son champ `name` exactement. Mets-le dans `REPEATER_NAME` dans `meshcore_to_domoticz.py`.

### 5. Tester la requête de télémétrie à la main

```bash
meshcli -j -t 192.168.1.XX -p 5000 req_telemetry "MonRepeteur"
```

Tu dois obtenir un JSON du genre :

```json
{
    "name": "MonRepeteur",
    "pubkey_pre": "xxxxxxxxxxxxxxxx",
    "lpp": [
        {"channel": 1, "type": "voltage",     "value": 4.05},
        {"channel": 1, "type": "temperature", "value": 18.2}
    ]
}
```

Si `temperature` manque, le firmware du répéteur ne l'expose pas (ou la permission de ton companion sur le répéteur est trop basse — voir la doc MeshCore sur `setperm`).

### 6. Configurer Domoticz

#### 6a. Activer le MQTT Client Gateway

Dans Domoticz : **Setup → Hardware → Add** :
- Name : `MQTT Bridge`
- Type : **MQTT Client Gateway with MQTT interface**
- Remote Address : `127.0.0.1`
- Port : `1883`
- Topic : `domoticz/in`

#### 6b. Créer les capteurs virtuels

**Setup → Hardware → Add** :
- Name : `MeshCore Repeater`
- Type : **Dummy (Does not create devices itself)**

Sur cette ligne, clic **Create Virtual Sensors** (deux fois) :
- `Tension batterie` → type **Voltage** → note l'**Idx** (ex. 100)
- `Température boîtier` → type **Temperature** → note l'**Idx** (ex. 101)

> ⚠️ **Important** : assure-toi que la ligne hardware est **activée** (pas grisée) — Domoticz te laisse silencieusement créer du hardware désactivé, et les capteurs apparaîtront dans *Devices* avec leurs valeurs, mais ne s'afficheront pas dans les onglets *Temperature* / *Utility*. (Demande-moi comment je le sais ;-) )

### 7. Déposer le script

```bash
sudo mkdir -p /opt/meshcore
sudo cp meshcore_to_domoticz.py /opt/meshcore/
sudo chmod +x /opt/meshcore/meshcore_to_domoticz.py
```

Édite `/opt/meshcore/meshcore_to_domoticz.py` et règle :
- `HELTEC_TCP_HOST` → l'IP de ton companion
- `REPEATER_NAME` → le nom exact du contact de l'étape 4
- `IDX_VOLTAGE`, `IDX_TEMPERATURE` → les idx Domoticz de l'étape 6b
- `MESHCLI_BIN` → le chemin vers ton binaire meshcli

### 8. Installer le timer systemd

```bash
sudo cp meshcore-poll.service meshcore-poll.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-poll.timer

# Verifier que le timer est programme
systemctl list-timers meshcore-poll
```

### 9. Premier lancement manuel + vérification

```bash
sudo systemctl start meshcore-poll.service
journalctl -u meshcore-poll.service -n 20 --no-pager
```

Tu dois voir :

```
... [INFO] Running: /home/pi/meshcore-venv/bin/meshcli -j -t 192.168.1.XX -p 5000 req_telemetry MonRepeteur
... [INFO] Telemetry: {"name": "MonRepeteur", "pubkey_pre": "...", "lpp": [...]}
... [INFO] Domoticz idx=100 svalue=4.05
... [INFO] Domoticz idx=101 svalue=18.2
```

Et dans Domoticz, les deux capteurs virtuels doivent se mettre à jour instantanément. Clic sur le nom d'un capteur dans **Setup → Devices** pour ouvrir le graphique — le premier point apparaît, la courbe grossit à chaque poll 3h.

### 10. (Optionnel) Surveiller le trafic MQTT en direct

```bash
mosquitto_sub -t domoticz/in -v
```

Tu verras chaque message `{"idx":N,"nvalue":0,"svalue":"..."}` au moment où le script le publie.

## Référence de configuration

Tous les réglages sont en haut de `meshcore_to_domoticz.py` :

| Constante | Défaut | Description |
|---|---|---|
| `TRANSPORT` | `"tcp"` | `"tcp"` (WiFi) ou `"serial"` (USB) |
| `HELTEC_TCP_HOST` | `"192.168.1.XX"` | IP du companion sur ton LAN |
| `HELTEC_TCP_PORT` | `5000` | Port TCP companion MeshCore (défaut) |
| `SERIAL_PORT` | `"/dev/ttyACM0"` | Utilisé seulement si `TRANSPORT="serial"` |
| `REPEATER_NAME` | `"MonRepeteur"` | Nom exact du contact dans le companion |
| `DOMOTICZ_MQTT_HOST` | `"127.0.0.1"` | Hôte du broker MQTT |
| `DOMOTICZ_MQTT_PORT` | `1883` | Port du broker MQTT |
| `DOMOTICZ_MQTT_TOPIC` | `"domoticz/in"` | Topic du gateway MQTT Domoticz |
| `IDX_VOLTAGE` | `100` | idx du capteur Voltage Domoticz |
| `IDX_TEMPERATURE` | `101` | idx du capteur Temperature Domoticz |
| `IDX_BATTERY_PCT` | `0` | idx optionnel % batterie (inutilisé sur la plupart des firmwares répéteur) |
| `MESHCLI_BIN` | `"/home/pi/meshcore-venv/bin/meshcli"` | Chemin vers meshcli |
| `MESHCLI_TIMEOUT` | `120` | Timeout d'appel meshcli (secondes) |

## Ajuster l'intervalle de polling

Le timer systemd se déclenche toutes les 3h. Pour changer ça, édite `meshcore-poll.timer` :

```ini
[Timer]
OnBootSec=2min
OnUnitActiveSec=1h    # <-- passer à 1h, 30min, 6h, etc.
```

Puis :

```bash
sudo systemctl daemon-reload
sudo systemctl restart meshcore-poll.timer
```

> 💡 **Note batterie** : le répéteur n'émet que lorsqu'on l'interroge. Un poll plus fréquent = plus de trafic mesh = un peu plus de conso batterie sur le répéteur. Pour un relais sur toit en 18650, 1h–3h est l'optimum. Du polling sub-15min est overkill.

## Dépannage

### `meshcli: No response from meshcore node, disconnecting`

Le firmware du companion n'expose probablement pas l'USB comme transport companion. Sur les builds MeshCore **v1.18.0+ BLE+WiFi**, l'USB sert juste aux logs série — utilise plutôt TCP :

```bash
meshcli -j -t <ip_companion> -p 5000 infos
```

### `ERROR:meshcore:Error in parameters`

Tu utilises probablement la mauvaise syntaxe pour `req_telemetry`. Le nom du contact se passe en **argument direct**, pas via `-d` :

```bash
# Faux
meshcli -j -t <ip> -p 5000 -d "MonRepeteur" req_telemetry

# Bon
meshcli -j -t <ip> -p 5000 req_telemetry "MonRepeteur"
```

### `meshcli req_telemetry` renvoie du JSON sans `temperature`

Soit :
- Le firmware du répéteur n'expose pas la température (certains builds répéteur pur ne le font pas). Sur les cartes nRF52, la température interne du MCU est généralement disponible — reflash avec un build sensor si besoin.
- La permission de ton companion sur le répéteur est trop basse. Depuis la CLI MeshCore du companion, connecte-toi au répéteur et accorde le niveau 2 à la clé publique du companion :
  ```
  login <nom_repéteur> <mot_de_passe_admin>
  setperm <clé_publique_companion> 2
  ```

### `pip install meshcore-cli` échoue avec "Requires Python >=3.10"

Ton Python système est trop vieux (typique sur Raspberry Pi OS Buster = Python 3.7). Installe Python 3.11 via `pyenv` sans toucher au Python système :

```bash
# Dépendances de compilation
sudo apt install -y build-essential libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev libffi-dev libncursesw5-dev \
  libgdbm-dev liblzma-dev tk-dev uuid-dev curl git

# Installer pyenv (lancer dans tmux au cas où la SSH coupe en pleine compile)
sudo apt install -y tmux
tmux new -s pyenv
curl https://pyenv.run | bash

# Ajouter à ~/.bashrc
cat >> ~/.bashrc << 'EOF'

# pyenv
export PYENV_ROOT="$HOME/.pyenv"
[ -d "$PYENV_ROOT" ] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$("$PYENV_ROOT/bin/pyenv" init -)"
EOF
source ~/.bashrc

# Compiler Python 3.11 (~25 min sur Pi 3, ~10 min sur Pi 4)
pyenv install 3.11.9
pyenv global 3.11.9
python3 --version    # doit afficher Python 3.11.9

# Créer le venv avec le nouveau Python
python3 -m venv ~/meshcore-venv
source ~/meshcore-venv/bin/activate
pip install meshcore-cli paho-mqtt
```

### `python3 -m venv` échoue avec une erreur `ensurepip`

Sur Debian/Raspbian Buster, `python3 -m venv` peut échouer parce que `ensurepip` est absent ou cassé. Contournement : installe `python3-venv` et `python3-pip` via apt d'abord, ou crée le venv avec le binaire Python explicite de pyenv :

```bash
~/.pyenv/versions/3.11.9/bin/python3 -m venv ~/meshcore-venv
```

### Dans le venv, `python --version` affiche une vieille version (3.7/3.8)

Tu as presque certainement un alias shell qui pirate la commande `python`. Vérifie :

```bash
type python
# si ça dit "python is aliased to '/usr/bin/python3.X'", c'est le coupable
unalias python
grep -rn 'alias python' ~/.bashrc ~/.bash_aliases ~/.profile /etc/profile.d/
```

Commente la ligne d'alias dans le fichier coupable, puis `source ~/.bashrc`. Le script lui-même appelle `python3` par chemin absolu donc l'alias ne le gêne pas, mais ça interfère avec les tests manuels.

### Les capteurs apparaissent dans l'onglet Devices de Domoticz mais pas dans Temperature / Utility

99 % du temps : la ligne hardware **Dummy** est **désactivée** (interrupteur gris). Active-la, les capteurs apparaîtront dans leurs onglets en quelques secondes.

Autres possibilités :
- Mauvais **Type/SubType** à la création du capteur virtuel (clic sur l'engrenage pour vérifier).
- Le capteur est marqué **Hidden** (engrenage → décocher "Hidden").

### Vieux dépôts apt Raspberry Pi OS Buster plus disponibles

```
E: The repository http://raspbian.raspberrypi.org/raspbian buster Release does not contain a Release file.
```

Édite `/etc/apt/sources.list` et remplace l'URL raspbian par l'archive legacy :

```
deb https://legacy.raspbian.org/raspbian/ buster main contrib non-free rpi
```

Puis `sudo apt update`.

## Comment ça marche

1. Le **timer systemd** déclenche `meshcore-poll.service` toutes les 3 heures.
2. Le service exécute `/home/pi/meshcore-venv/bin/python3 /opt/meshcore/meshcore_to_domoticz.py`.
3. Le script Python invoque `meshcli -j -t <ip_companion> -p 5000 req_telemetry <nom_répéteur>`.
4. `meshcli` se connecte au companion (Heltec v4) en TCP, envoie une requête de télémétrie.
5. Le companion relaie la requête sur le mesh LoRa vers le répéteur.
6. Le répéteur répond avec un payload Cayenne LPP (tension batterie + température).
7. Le companion relaie la réponse à `meshcli`, qui l'affiche en JSON.
8. Le script Python parse le tableau JSON `lpp`, extrait `voltage` et `temperature`, et les publie au broker MQTT avec l'idx Domoticz correspondant.
9. Le MQTT Client Gateway de Domoticz reçoit les messages et met à jour les capteurs virtuels. Les graphiques grossissent à chaque point.

Coût total par poll : ~5 secondes d'airtime mesh (un aller-retour), toutes les 3 heures. Impact batterie négligeable sur le répéteur.

## Licence

MIT — voir [LICENSE](LICENSE).

## Remerciements

- [MeshCore](https://meshcore.io) — le firmware qui anime le répéteur et le companion
- [meshcore-cli](https://github.com/meshcore-dev/meshcore-cli) — la CLI Python utilisée pour parler au companion
- [Domoticz](https://www.domoticz.com) — le système domotique
- [Mosquitto](https://mosquitto.org) — le broker MQTT
- [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) — le client MQTT Python

## Voir aussi

- [meshcore-ha](https://github.com/meshcore-dev/meshcore-ha) — intégration officielle Home Assistant (si tu es sur HA, tu n'as pas besoin de ce projet)
- [awesome-meshcore](https://github.com/meshcore-ita/awesome-meshcore) — liste curatée d'outils et intégrations MeshCore
