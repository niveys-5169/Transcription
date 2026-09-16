from app.proofread.structure import insert_headings, insert_wikilinks, parse_json_object


TEXTE = (
    "Bonjour à tous, nous allons commencer ce cours de thermodynamique.\n\n"
    "Le premier principe énonce que l'énergie totale d'un système isolé "
    "se conserve au cours du temps.\n\n"
    "Le second principe introduit la notion d'entropie, qui ne peut que "
    "croître dans un système isolé."
)


def test_parse_json_object_nu():
    assert parse_json_object('{"title": "Cours"}') == {"title": "Cours"}


def test_parse_json_object_dans_un_bloc_de_code():
    raw = 'Voici :\n```json\n{"title": "Cours", "summary": []}\n```\nVoilà.'
    assert parse_json_object(raw)["title"] == "Cours"


def test_parse_json_object_avec_du_bavardage_autour():
    raw = 'Bien sûr ! {"title": "Cours"} J\'espère que cela convient.'
    assert parse_json_object(raw) == {"title": "Cours"}


def test_parse_json_object_sur_entree_invalide():
    assert parse_json_object("pas du json du tout") == {}
    assert parse_json_object("") == {}
    assert parse_json_object("[1, 2, 3]") == {}


def test_insert_headings_place_les_titres_au_bon_endroit():
    sections = [
        {"heading": "Le premier principe", "quote": "Le premier principe énonce que l'énergie"},
        {"heading": "Le second principe", "quote": "Le second principe introduit la notion"},
    ]
    resultat = insert_headings(TEXTE, sections)
    assert "## Le premier principe\n\nLe premier principe énonce" in resultat
    assert "## Le second principe\n\nLe second principe introduit" in resultat


def test_insert_headings_ne_modifie_jamais_le_texte():
    sections = [
        {"heading": "Le second principe", "quote": "Le second principe introduit la notion"}
    ]
    resultat = insert_headings(TEXTE, sections)
    sans_titres = "\n\n".join(
        bloc for bloc in resultat.split("\n\n") if not bloc.startswith("## ")
    )
    assert sans_titres == TEXTE


def test_insert_headings_tolere_une_citation_mal_recopiee():
    # Ponctuation et casse différentes : la recherche doit quand même aboutir.
    sections = [{"heading": "Second principe", "quote": "le second principe, introduit la Notion"}]
    assert "## Second principe" in insert_headings(TEXTE, sections)


def test_insert_headings_ignore_une_citation_introuvable():
    sections = [{"heading": "Hors sujet", "quote": "cette phrase n'existe nulle part ici"}]
    assert insert_headings(TEXTE, sections) == TEXTE


def test_insert_headings_ignore_les_citations_trop_courtes():
    assert insert_headings(TEXTE, [{"heading": "Trop court", "quote": "Le"}]) == TEXTE


def test_insert_headings_ignore_une_section_qui_reculerait():
    sections = [
        {"heading": "Le second principe", "quote": "Le second principe introduit la notion"},
        {"heading": "Retour en arrière", "quote": "Le premier principe énonce que l'énergie"},
    ]
    resultat = insert_headings(TEXTE, sections)
    assert "## Retour en arrière" not in resultat
    assert "## Le second principe" in resultat


def test_insert_headings_ne_titre_pas_le_tout_debut():
    sections = [{"heading": "Introduction", "quote": "Bonjour à tous, nous allons commencer"}]
    assert insert_headings(TEXTE, sections) == TEXTE


def test_insert_headings_sans_sections():
    assert insert_headings(TEXTE, []) == TEXTE
    assert insert_headings("", [{"heading": "x", "quote": "y" * 20}]) == ""


def test_wikilinks_sont_inseres_une_fois_vers_une_note_indexee():
    text = "La curatelle renforcée protège la personne. La curatelle renforcée est encadrée."
    result = insert_wikilinks(text, [{"quote": "protège la personne", "title": "Curatelle renforcée"}], {"Curatelle renforcée"})
    assert "[[Curatelle renforcée|protège la personne]]" in result


def test_wikilinks_refusent_une_cible_inconnue_ou_une_citation_ambigue():
    text = "Le même terme. Le même terme."
    assert insert_wikilinks(text, [{"quote": "Le même terme", "title": "Inconnue"}], {"Connue"}) == text
    assert insert_wikilinks(text, [{"quote": "Le même terme", "title": "Connue"}], {"Connue"}) == text
