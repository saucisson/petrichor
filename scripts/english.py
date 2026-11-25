"""English-only front: every development artifact stays English (ADR-0008 G4, N4).

Development artifacts are English by convention (G4): written French
can be accent-free (``defaut``, ``serialiser``, ``noeud``), so a bare
accented-character grep catches only part of the problem. This front
keeps the accented signal and adds an unaccented-French signal, with
its signal and corpus pairs adapted from the lisage checker
(saucisson/lisage, issues #104 and #110) and calibrated on this
template's tree:

- accented characters: every file in scope;
- French identifier stems: Python code files only, matched against
  the parts of identifiers (names) and of comments, never string
  literals: literals carry wire bytes and command-line names, which
  are data, not prose;
- French stop-words: prose only - comments and plain-text config - so
  no English identifier ever trips them.

In Markdown, fenced code blocks and inline code spans are skipped, so
wire-format examples can be quoted verbatim inside code formatting.

The subcommand takes two shapes:

- ``python -m scripts english-only`` and ``make english-only``: scan
  the whole scope, print one line per offender, exit 1 as soon as one
  is found - the CI and ``make check`` front;
- ``python -m scripts english-only --subject-file PATH``: scan only
  the first line of that file, accents and stop-words, exit 1 on an
  offender - the commit-msg hook shape (N4): a commit subject is an
  artifact like any other, and the template hooks it in
  ``.pre-commit-config.yaml``.

An instance that must keep an in-scope file in French argues the
addition to ``ALLOWED_EXACT`` per PR, reviewed like any change; lock
files are machine-generated and never scanned.

The package targets Python 3.15: the sources use unparenthesized
multi-exception ``except`` clauses, which parse only from Python 3.14,
so nothing under 3.14 can import ``scripts``.
"""

import dataclasses
import re
import sys
import tokenize
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
    REPOSITORY_ROOT,
)

if TYPE_CHECKING:
    import argparse


# The accented signal, built from code points so this file itself
# stays accent-free: only the letters that written French uses. Broader
# ranges (Latin-1 Supplement, Latin Extended-A) flag legitimate
# non-French quotations, such as Danish bibliographic names.
_ACCENT_LOWER = "".join(
    chr(code_point)
    for code_point in (
        0xE0,
        0xE2,
        0xE4,
        0xE7,
        0xE9,
        0xE8,
        0xEA,
        0xEB,
        0xEE,
        0xEF,
        0xF4,
        0xF9,
        0xFB,
        0xFC,
        0xFF,
        0x153,
        0xE6,
    )
)
_ACCENT_UPPER = _ACCENT_LOWER.upper()
ACCENTS = _ACCENT_LOWER + _ACCENT_UPPER
ACCENT_RE = re.compile(f"[{re.escape(ACCENTS)}]")

# French function words, as whole words only: they catch accent-free
# French prose in comments and plain-text config. None of them appears
# as a whole word in the English code of this template.
_STOP_WORD_LEXICON = (
    "le la les un une des du de en et ou est sont dans sur pour avec par "
    "pas mais donc car quand si nous vous ici"
)
STOP_WORDS = _STOP_WORD_LEXICON.split()

# French identifier stems, matched against snake-case and CamelCase
# parts of identifiers. Calibrated on the saucisson repositories:
# an instance extends the set when its own renames surface new stems,
# and keeps it free of legitimate English words (``value``, ``image``
# stay out of such a set on purpose).
_FRENCH_LEXICON = """
    absente absents absolu absolue absolues accepte actif actifs adopte
    affectation aire aleatoire aleatoires aligne alignement alignes aller
    alloues amorce anchorcomposee ancienne ancres anneau antisymetrique
    appartenance applique appliquee appliquer apres arbre arcmaloriente
    arene arenes atelier atome atomes atteignable atteignables atteint
    attendu attendue attendus attraper attribut aucun aucune autodetectee
    autorisee autre autres aux aval avant avec balayage balayageunivers
    balise banc bande barre boolou bord borne bornes boucle bout branche
    brut canal canonicite canonique canoniques caractere cardinaux carte
    cartes cellules chaine champ champs charger chemin cherche cherches
    choix cible classe classeur classique cle clef clefs cles cloture
    cloturee coherents collectes comme comparaison compatibilite
    complementaire composee composees comptage compte compter compteur
    constante constantes construire construit construits contenu contexte
    contextephiout contextualiser contrainte contraintes contrat contre
    controleur copie corps couleur couleurs courante cout couture coutures
    couvre croissance croissante curseur dans de debut decalage decalages
    decalee decremente defaut defauts deja demi densite dependances depliee
    deplier depuis dernier derniere des descripteur deserialise deserialiser
    dessine dessiner dessous determinisme deterministe deux diagonale
    diagramme differentes differentiel directe disperser distinctes
    distincts domainatomes domaine doublee doublon droite du duree echappe
    echappee echapper echelle echoue ecrit ecriture ecriturememory ecritures
    effet egal egale egales egalite emet empreinte empreintes encodage encre
    ensemble ensembles entier entiers entree entrees enveloppe epingle
    epinglee epingles equilibree equivaut erreur espace essai est et etape
    etat etats etendu etendus etiqueter etiquette etiquettes etrangere
    evalue evenement evenementielle evenements exacte exactes executer
    existe expectede explicite exportee extraire extrait extraits facteur
    factice famille faux fenetre fenetres fermante fermer fermeture feuille
    feuilles fichier fichiers fil filtre filtrer fin fixe fleche fois
    fonction fond forme frise frontiere frontieres garde gauche glessee
    glissante glisse glissee glisses grande greffe greffer greffon gross
    hachage hauteur herite herites hierarchie horloges hors idempotente
    identifiant identique identite idsaleatoires inchangee incluse
    incoherent incoherente inconnu indexee indice inferieure integrale
    intercalage intercale interne internement internements invalide
    invalidee invalides jeton jetons joue jusqua justifie keypresentes
    largeur lecture lectures legales legende les libelle libre ligne lignes
    lineaire liste litterale locaux longueur majore mal malformees mange
    mangent mangeurs manquant manquante marge marquage marquages marque
    marquebalayage marquecanonique marqueencodage marqueenumeration
    marqueorder marqueur matrice meme memoire memoires memorynonautorisee
    mesure mesurer metriques miroir miroirs modele modifie modifies montant
    mots multiplicateur multiplicite naif naturel nb neuf neutre niveau
    niveaux noeud noeuds nom nombre noms nul nulle numero obtenu obtenue
    obtenus octet octets ombre ordersinconvertibles ordre oriente orientee
    ou ouvert ouverte ouvrante ouvrir paire paires panneau parametres
    parametresinvalides parametresoracle parametressaturation partage
    partages parties pas pasunepermutation pave personnalise petit piste pli
    pliage plier plis plusieurs poids pondere ponderee ponderees porteur
    porteurs pour pourcentage precedent precedente predicat predire premier
    premiere prendre presentes prevu prevue produit profilee profondeur
    propagee propriete proprietes quand racine rang rationnel rebords recree
    recu recus redondance reduire reduit reetiquetage reetiquete reetiquetes
    refusee registre regle regles rejet rejete rejette relatif relatifs
    rendre reordonner repli replicanonique reproductibles requetes requise
    reseau resoudre reste restreint restreinte resultat resultats retour
    retrilles retrillie rouge sans satisfait saturer seconde semantique
    semianneau sensibilisation sensibilise sensibilisee sept serialisation
    serialise serialisee serialiser seuil seul seule si socle socles sortie
    sorties sous stateetendu statique stockage strategie strategieaval
    successeur suivant suivante suivants superieure supporte sur surcharge
    synthetise synthetiseur taille temoin temoins terminaison terminaux
    termine texte textes tir tirer titre touche tous tout toutes
    transducteur transfert transparente tri triee triees trier triptyque
    trois tronque tronques trouvees typage typee typees un une
    unidirectionnelles unite univers valeur valeurs valide validee valider
    variableendouble variante vaut verifie verifiee verifieechoue vers
    versionnee violee visite visites vivantes vivants voie vrai vu vue vus
"""
FRENCH_IDENTIFIERS = frozenset(_FRENCH_LEXICON.split())

# The scan scope of the convention: root Markdown, docs in full, the
# driver package and its tests, the notify tool sources and tests,
# .github Markdown and .gitignore.
SCOPE_GLOBS = (
    "*.md",
    ".gitignore",
    "docs/*",
    "docs/**/*",
    "scripts/**/*.py",
    "tools/*/src/**/*.py",
    "tools/*/tests/**/*.py",
    ".github/**/*.md",
)

# Deliberately allowed to hold French (argued per PR, see the module
# docstring); empty in the template itself: its tree scans clean.
# Instance adaptation (petrichor): history predates the front. Every
# file written before the front joined the repository holds French
# prose and stays allowed, listed exhaustively; every file created
# after the front must scan clean, and a file leaves this list only
# by an argued translation pull request.
ALLOWED_EXACT: frozenset[str] = frozenset({
    "docs/agents/petrichor.md",
    "docs/backlog.md",
    "docs/consigne-interface-web-v1.md",
    "docs/decisions/adr-0010-agent-protocol.md",
    "docs/decisions/adr-0101-ui-v2-react-typescript-vite.md",
    "docs/decisions/adr-0104-ui-v3-tailwind-css.md",
    "docs/decisions/adr-0107-ui-amorcage-import.md",
    "docs/spec/petrichor.md",
    "docs/spikes/2026-11-12-lola2-fairness-spike.md",
    "docs/spikes/2026-11-12-ty-spike.md",
    "docs/spikes/2026-11-18-p39-mock-vs-real-e2e.md",
    "docs/spikes/2026-11-19-adr-selection-in-review-bundle.md",
    "docs/spikes/2026-11-19-reviewer-reliability.md",
    "docs/spikes/2026-11-20-catalogues-mcc.md",
    "docs/studies/pilote/v1/audit/addendum-2026-11-20-f5-ui-observee.md",
    "docs/studies/pilote/v1/audit/decision.md",
    "docs/studies/pilote/v1/audit/frictions.md",
    "docs/studies/pilote/v1/execution/contrat.md",
    "docs/ui-mockup/README.md",
    "docs/ui-mockup/philosophes-3.tsx",
    "docs/ui-mockup/v3-chrome/README.md",
    "docs/worklog/2026-11-13-pr-101.md",
    "docs/worklog/2026-11-13-pr-102.md",
    "docs/worklog/2026-11-17-pr-103.md",
    "docs/worklog/2026-11-17-pr-104.md",
    "docs/worklog/2026-11-17-pr-106.md",
    "docs/worklog/2026-11-17-pr-110.md",
    "docs/worklog/2026-11-17-pr-111.md",
    "docs/worklog/2026-11-18-pr-112.md",
    "docs/worklog/2026-11-18-pr-113.md",
    "docs/worklog/2026-11-18-pr-114.md",
    "docs/worklog/2026-11-18-pr-115.md",
    "docs/worklog/2026-11-18-pr-116.md",
    "docs/worklog/2026-11-18-pr-117.md",
    "docs/worklog/2026-11-18-pr-118.md",
    "docs/worklog/2026-11-18-pr-119.md",
    "docs/worklog/2026-11-19-pr-120.md",
    "docs/worklog/2026-11-19-pr-121.md",
    "docs/worklog/2026-11-19-pr-122.md",
    "docs/worklog/2026-11-19-pr-136.md",
    "docs/worklog/2026-11-19-pr-137.md",
    "docs/worklog/2026-11-19-pr-138.md",
    "docs/worklog/2026-11-19-pr-140.md",
    "docs/worklog/2026-11-19-pr-141.md",
    "docs/worklog/2026-11-19-pr-142.md",
    "docs/worklog/2026-11-19-pr-143.md",
    "docs/worklog/2026-11-19-pr-145.md",
    "docs/worklog/2026-11-20-pr-150.md",
    "docs/worklog/2026-11-20-pr-152.md",
    "docs/worklog/2026-11-20-pr-153.md",
    "docs/worklog/2026-11-20-pr-154.md",
    "docs/worklog/2026-11-20-pr-155.md",
    "docs/worklog/2026-11-20-pr-168.md",
    "docs/worklog/2026-11-20-pr-169.md",
    "docs/worklog/2026-11-20-pr-170.md",
    "docs/worklog/2026-11-20-pr-171.md",
    "docs/worklog/2026-11-20-pr-172.md",
    "docs/worklog/2026-11-20-pr-175.md",
    "docs/worklog/2026-11-21-pr-191.md",
    "docs/worklog/2026-11-21-pr-192.md",
    "docs/worklog/2026-11-21-pr-193.md",
    "docs/worklog/2026-11-21-pr-194.md",
    "docs/worklog/2026-11-21-pr-195.md",
    "docs/worklog/2026-11-21-pr-196.md",
    "docs/worklog/2026-11-21-pr-199.md",
    "docs/worklog/2026-11-21-pr-201.md",
    "docs/worklog/2026-11-21-pr-202.md",
    "docs/worklog/2026-11-21-pr-203.md",
    "docs/worklog/2026-11-21-pr-204.md",
    "docs/worklog/2026-11-21-pr-p75.md",
    "docs/worklog/2026-11-23-pr-213.md",
    "docs/worklog/2026-11-23-pr-217.md",
    "docs/worklog/2026-11-24-pr-220.md",
    "docs/worklog/2026-11-24-pr-221.md",
    "docs/worklog/2026-11-24-pr-222.md",
    "docs/worklog/archive-2026-11-12.md",
    "scripts/create_issues.py",
    "scripts/tests/test_create_issues.py",
    "scripts/tests/test_review.py",
})

_TEXT_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf"})

_FENCE_RE = re.compile(r"(?ms)^(```|~~~)[^\n]*?$\n.*?^\1[^\n]*?$")
INLINE_SPAN_RE = re.compile(r"`[^`\n]*`")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_PART_SPLIT_RE = re.compile(r"[_0-9]+|(?<=[a-z])(?=[A-Z])")

# On Python 3.12+ (PEP 701) f-strings tokenize into start, middle and
# end tokens; the middles are the literal text, so they join the
# string corpus and a French label inside an f-string cannot hide.
_STRING_TOKEN_TYPES = frozenset({tokenize.STRING, tokenize.FSTRING_MIDDLE})

COMMIT_SUBJECT_ANCHOR = Path("<commit-subject>")


@dataclasses.dataclass(frozen=True)
class Offender:
    """One French finding: file, line and what matched."""

    relative: Path
    line: int
    evidence: str

    def __str__(self) -> str:
        """Render the finding as ``path:line: evidence``."""
        location = self.relative.as_posix()
        if self.line:
            location = f"{location}:{self.line}"
        return f"{location}: {self.evidence}"


def is_allowed(relative: Path) -> bool:
    """Tell whether a scanned file is deliberately allowed to hold French."""
    if relative.as_posix() in ALLOWED_EXACT:
        return True
    return relative.suffix == ".lock"


def markdown_without_code_spans(text: str) -> str:
    """Strip fenced code blocks and inline code spans from Markdown.

    Wire-format examples are quoted inside code formatting on
    purpose: the front must not flag them.
    """
    stripped = _FENCE_RE.sub(" ", text)
    return INLINE_SPAN_RE.sub(" ", stripped)


def python_corpus(source: str) -> tuple[list[str], list[str], list[str]]:
    """Split a Python source into (names, comments, strings).

    Names (identifiers) are scanned for French stems, comments for
    stop-words and stems, strings for accents only: a literal may
    legitimately quote a French label or a wire-format key.
    """
    names: list[str] = []
    comments: list[str] = []
    strings: list[str] = []
    for token in tokenize.generate_tokens(StringIO(source).readline):
        if token.type == tokenize.NAME:
            names.append(token.string)
        elif token.type == tokenize.COMMENT:
            comments.append(token.string)
        elif token.type in _STRING_TOKEN_TYPES:
            strings.append(token.string)
    return names, comments, strings


def _line_of(text: str, position: int) -> int:
    """Return the 1-based line number of a character position."""
    return text.count("\n", 0, position) + 1


def _first_accent(text: str) -> tuple[int, str] | None:
    """Return the position and evidence of the first accented character."""
    match = ACCENT_RE.search(text)
    if match is None:
        return None
    start = match.start()
    snippet = text[max(0, start - 20) : start + 21].replace("\n", " ")
    return start, f"accented character: ...{snippet}..."


def _first_stop_word(text: str) -> tuple[int, str] | None:
    """Return the position of the first French function word, if any.

    Boundaries exclude letters, digits and hyphens: a function word
    never abuts a digit in French prose, and this keeps pinned commit
    SHAs (``...6de127b...``) in plain-text files from tripping the
    scan.
    """
    for word in STOP_WORDS:
        match = re.search(rf"(?<![A-Za-z0-9-]){word}(?![A-Za-z0-9-])", text)
        if match is not None:
            return match.start(), f"French stop-word: {word}"
    return None


def _find_french_part(identifier: str) -> str | None:
    """Return the first French stem found in a snake or CamelCase part."""
    parts = _PART_SPLIT_RE.split(identifier)
    for part in parts:
        if part.lower() in FRENCH_IDENTIFIERS:
            return f"French identifier: {identifier}"
    return None


def scan_corpus(
    relative: Path,
    text: str,
    *,
    stop_words: bool,
    identifiers: bool,
) -> Offender | None:
    """Scan one text corpus; return the first offender found, if any."""
    finding = _first_accent(text)
    if finding is not None:
        position, evidence = finding
        return Offender(relative, _line_of(text, position), evidence)
    if stop_words:
        finding = _first_stop_word(text)
        if finding is not None:
            position, evidence = finding
            return Offender(relative, _line_of(text, position), evidence)
    if identifiers:
        for match in IDENTIFIER_RE.finditer(text):
            finding_text = _find_french_part(match.group(0))
            if finding_text is not None:
                return Offender(relative, _line_of(text, match.start()), finding_text)
    return None


def scan_python(relative: Path, text: str) -> Offender | None:
    """Scan a Python file through its three corpora."""
    try:
        names, comments, strings = python_corpus(text)
    except SyntaxError, tokenize.TokenError, IndentationError:
        return scan_corpus(relative, text, stop_words=True, identifiers=False)
    offender = scan_corpus(relative, "\n".join(comments), stop_words=True, identifiers=True)
    if offender is not None:
        return offender
    offender = scan_corpus(relative, "\n".join(strings), stop_words=False, identifiers=False)
    if offender is not None:
        return offender
    return scan_corpus(relative, " ".join(names), stop_words=False, identifiers=True)


def scan_file(relative: Path) -> Offender | None:
    """Scan one in-scope file according to its type."""
    if relative.suffix in _TEXT_SUFFIXES:
        return None
    try:
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None  # Binary or unreadable (logos, fixtures): out of scope.
    suffix = relative.suffix
    if suffix == ".py":
        return scan_python(relative, text)
    if suffix == ".md":
        return scan_corpus(
            relative, markdown_without_code_spans(text), stop_words=False, identifiers=False
        )
    # Plain-text configuration: prose only, so stop-words apply and
    # identifiers do not.
    stop_words = relative.name == ".gitignore" or relative.name.startswith("Dockerfile")
    return scan_corpus(relative, text, stop_words=stop_words, identifiers=False)


def iter_scope() -> list[Path]:
    """List every in-scope file, relative to the repository root."""
    seen: dict[Path, None] = {}
    for pattern in SCOPE_GLOBS:
        for path in REPOSITORY_ROOT.glob(pattern):
            if path.is_file():
                seen[path.relative_to(REPOSITORY_ROOT)] = None
    return list(seen)


def scan_repository() -> list[Offender]:
    """Scan the whole scope; return every offender found."""
    offenders: list[Offender] = []
    for relative in iter_scope():
        if is_allowed(relative):
            continue
        offender = scan_file(relative)
        if offender is not None:
            offenders.append(offender)
    return offenders


def scan_subject(*, subject: str) -> Offender | None:
    """Scan a commit subject: accented characters and French stop-words.

    The subject is prose, so the stem corpus stays out of it and
    identifiers with their own naming rules never apply.
    """
    finding = _first_accent(subject)
    if finding is None:
        finding = _first_stop_word(subject)
    if finding is None:
        return None
    position, evidence = finding
    return Offender(COMMIT_SUBJECT_ANCHOR, _line_of(subject, position), evidence)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the english-only subcommand."""
    subparser = subparsers.add_parser(
        "english-only",
        help="Fail on French text over the repository (ADR-0008 G4, N4).",
        description="Fail on French text over the repository (ADR-0008 G4, N4).",
    )
    subparser.add_argument(
        "--subject-file",
        default=None,
        help="Commit-msg hook shape: scan only the first line of this file.",
    )
    subparser.set_defaults(runner=run)


def run(*, arguments: argparse.Namespace) -> int:
    """Entry point: whole-scope scan, or one-subject scan for the hook."""
    if arguments.subject_file is None:
        offenders = scan_repository()
        for offender in offenders:
            print(f"english-only: {offender}")
        if offenders:
            print(
                f"english-only: {len(offenders)} offender(s): development"
                " artifacts are English-only (ADR-0008 G4, N4)."
            )
            return EXIT_CODE_CRITERIA_UNMET
        return EXIT_CODE_SUCCESS
    try:
        message_text = Path(arguments.subject_file).read_text(encoding="utf-8")
    except OSError:
        print(
            f"Error: could not read the commit message file: {arguments.subject_file}.",
            file=sys.stderr,
        )
        return EXIT_CODE_PROTOCOL_FAILURE
    subject_lines = message_text.splitlines()
    subject = subject_lines[0] if subject_lines else ""
    subject_offender = scan_subject(subject=subject)
    if subject_offender is not None:
        print(f"english-only: {subject_offender}")
        print("english-only: the commit subject must be English (ADR-0008 N4).")
        return EXIT_CODE_CRITERIA_UNMET
    return EXIT_CODE_SUCCESS
