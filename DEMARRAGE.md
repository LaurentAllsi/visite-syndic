# Visite Syndic — Démarrage

## Installation (une seule fois)

```
cd "C:\Users\laure\OneDrive\Documents\Claude\Syndic\visite-app"
pip install flask
```

Pour la génération PDF côté serveur (optionnel) :
```
pip install xhtml2pdf
```
> Sans ces librairies, le PDF s'ouvre dans le navigateur et peut être imprimé/sauvegardé via Ctrl+P.

## Lancement

```
cd "C:\Users\laure\OneDrive\Documents\Claude\Syndic\visite-app"
python app.py
```

Puis ouvrir dans le navigateur : **http://localhost:5001**

Depuis un autre poste du réseau : **http://[IP_DU_PC]:5001**

## Connexion

- **Mot de passe** : `laforet2024` (identique à facturation)
- Chaque collaborateur saisit son prénom à la connexion — il sera affiché comme rédacteur

## Import des copropriétés depuis Facturation

Dans l'onglet **Copropriétés** → bouton **Importer facturation**
L'import ajoute uniquement les nouvelles copropriétés (pas de doublon).

## Port

L'app tourne sur le port **5001** pour ne pas entrer en conflit avec Facturation (port 5000).

## Données

Les visites et observations sont stockées dans :
`data/visite.db`

Ce fichier est sauvegardé automatiquement avec OneDrive.
