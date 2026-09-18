#!/usr/bin/env bash
# Installation complète macOS / Linux : prérequis, dépendances, .env, vérification.
# Usage :  bash scripts/setup.sh
set -e
cd "$(dirname "$0")/.."

have() { command -v "$1" >/dev/null 2>&1; }

echo "== Prérequis =="
if [[ "$OSTYPE" == darwin* ]]; then
  have brew || { echo "Homebrew requis : https://brew.sh"; exit 1; }
  have python3 || brew install python
  have node    || brew install node
  have ffmpeg  || brew install ffmpeg
  have git     || brew install git
else
  have python3 || sudo apt-get install -y python3 python3-pip
  have node    || { echo "Installez Node.js 22+ : https://nodejs.org"; exit 1; }
  have ffmpeg  || sudo apt-get install -y ffmpeg
  have git     || sudo apt-get install -y git
fi

echo "== Dépendances Python =="
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt

echo "== Dépendances Node (HyperFrames) =="
npm install

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Fichier .env créé : renseignez PEXELS_API_KEY (clé gratuite sur https://www.pexels.com/api/)."
fi

echo "== Vérification =="
python3 -m clipper doctor
