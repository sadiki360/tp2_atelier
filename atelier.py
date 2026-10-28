"""Supervision d'un atelier : référentiel des machines, relevés capteurs, alertes et rapport."""
import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path

from exceptions import FichierInvalide, ReleveInvalide

COLONNES = ("machine", "horodatage", "temperature", "vibration")

# Un relevé valide est un tuple : (machine, horodatage, temperature, vibration)
Releve = tuple[str, datetime, float, float]


# --- Étape 1 : le référentiel des machines (JSON, dictionnaires, ensembles) --------------------
def charger_machines(chemin):
    with open(chemin, encoding="utf-8") as f:
        fiches = json.load(f)
    machines = {}
    for fiche in fiches:
        if fiche["id"] in machines:
            raise FichierInvalide(f"identifiant en double : {fiche['id']}")
        machines[fiche["id"]] = fiche
    return machines

def ateliers(machines):
    return {fiche["atelier"] for fiche in machines.values()}


# --- Étape 3 : valider une ligne (exceptions, tuples) -------------------------------------------
def lire_nombre(texte, champ, numero):
    texte = texte.strip()
    if not texte:
        raise ReleveInvalide("valeur manquante", numero, champ)
    try:
        valeur = float(texte.replace(",", "."))
    except ValueError:
        raise ReleveInvalide(f"valeur non numérique : {texte!r}", numero, champ) from None
    if not math.isfinite(valeur):
        raise ReleveInvalide(f"valeur non finie : {texte!r}", numero, champ)
    return valeur


def parser_releve(brut, numero, machines):
    machine = brut["machine"].strip().upper()
    if machine not in machines:
        raise ReleveInvalide(f"machine inconnue : {machine!r}", numero, "machine")

    try:
        horodatage = datetime.fromisoformat(brut["horodatage"].strip())
    except ValueError:
        raise ReleveInvalide(
            f"horodatage invalide : {brut['horodatage']!r}", numero, "horodatage"
        ) from None

    temperature = lire_nombre(brut["temperature"], "temperature", numero)
    vibration = lire_nombre(brut["vibration"], "vibration", numero)
    if vibration < 0:
        raise ReleveInvalide(f"vibration négative : {vibration!r}", numero, "vibration")

    return (machine, horodatage, temperature, vibration)


# --- Étape 4 : lire le fichier (CSV, générateur, collecte des erreurs) ---------------------------
def lignes_csv(chemin):
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        lecteur = csv.DictReader(f, delimiter=";")
        manquantes = [c for c in COLONNES if c not in (lecteur.fieldnames or [])]
        if manquantes:
            raise FichierInvalide(f"colonnes manquantes : {', '.join(manquantes)}")
        for numero, brut in enumerate(lecteur, start=2):
            yield numero, brut


def lire_releves(chemin, machines):
    releves = []
    erreurs = []
    for numero, brut in lignes_csv(chemin):
        try:
            releves.append(parser_releve(brut, numero, machines))
        except ReleveInvalide as err:
            erreurs.append(err)
    return releves, erreurs


def ecrire_rejets(erreurs, chemin):
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["ligne", "champ", "message"])
        for err in erreurs:
            writer.writerow([err.ligne, err.champ, err.message])


# --- Étape 5 : analyser (dictionnaires, compréhensions) ------------------------------------------
def par_machine(releves):
    groupes = {}
    for releve in releves:
        groupes.setdefault(releve[0], []).append(releve)
    return groupes

def moyennes(releves):
    return {
        machine: (
            round(sum(r[2] for r in liste) / len(liste), 1),
            round(sum(r[3] for r in liste) / len(liste), 2),
        )
        for machine, liste in par_machine(releves).items()
    }


def alertes(releves, machines):
    par_temp = [
        (m, h, f"température {t} > {machines[m]['temp_max']}")
        for m, h, t, v in releves
        if t > machines[m]["temp_max"]
    ]
    par_vib = [
        (m, h, f"vibration {v} > {machines[m]['vib_max']}")
        for m, h, t, v in releves
        if v > machines[m]["vib_max"]
    ]
    return sorted(par_temp + par_vib, key=lambda a: (a[1], a[0]))


def machines_silencieuses(releves, machines):
    return set(machines) - {r[0] for r in releves}


# --- Étape 6 : trier ---------------------------------------------------------------------------
def classement(releves, machines):
    compteurs = {m: 0 for m in machines}
    for machine, _, _ in alertes(releves, machines):
        compteurs[machine] += 1
    return sorted(compteurs.items(), key=lambda c: (-c[1], c[0]))


def top_vibrations(releves, n=3):
    return sorted(releves, key=lambda r: r[3], reverse=True)[:n]


# --- Étape 7 : le rapport JSON ------------------------------------------------------------------
def construire_rapport(releves, erreurs, machines):
    return {
        "releves_valides": len(releves),
        "lignes_rejetees": len(erreurs),
        "moyennes": {
            m: {"temperature": t, "vibration": v}
            for m, (t, v) in sorted(moyennes(releves).items())
        },
        "alertes": [
            {"machine": m, "heure": h.strftime("%H:%M"), "motif": motif}
            for m, h, motif in alertes(releves, machines)
        ],
        "classement": [m for m, n in classement(releves, machines) if n > 0],
        "machines_silencieuses": sorted(machines_silencieuses(releves, machines)),
    }


def ecrire_rapport(rapport, chemin):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2)


# --- Étape 8 (à la maison) : le journal des événements (expressions régulières) ------------------
MOTIF_EVENEMENT = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<heure>\d{2}:\d{2}:\d{2}) "
    r"\[(?P<machine>M-\d{2})\] "
    r"(?P<type>[A-Z]+) "
    r"code=(?P<code>[A-Z]\d{2})"
    r"(?: duree=(?P<duree>\d+))?"
)


def lire_evenements(chemin):
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            m = MOTIF_EVENEMENT.fullmatch(ligne.strip())
            if m is None:
                continue
            duree = m["duree"]
            yield {
                "machine": m["machine"],
                "type": m["type"],
                "code": m["code"],
                "heure": m["heure"],
                "duree": int(duree) if duree is not None else None,
            }


def temps_arret(evenements):
    totaux = {}
    for e in evenements:
        if e["type"] == "ARRET" and e["duree"] is not None:
            totaux[e["machine"]] = totaux.get(e["machine"], 0) + e["duree"]
    return totaux
