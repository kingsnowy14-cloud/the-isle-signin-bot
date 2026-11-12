"""English Discord mini-games for Evrima Bot.

Rounds are scoped to a guild/channel. Scores are persisted in the existing SQLite database.
"""
import asyncio
import json
import random
import re
import sqlite3
import unicodedata

import discord
import aiohttp
from discord import app_commands
from discord.ext import commands


GAMES = {
    "count": "Counting",
    "flag": "Flag Guessing",
    "dinoquiz": "Dino Guessing",
    "quiz": "Quiz",
    "hangman": "Hangman",
    "scramble": "Word Scramble",
}

# ISO 3166-1 sovereign-state list (193 UN members plus Palestine and Vatican City).
# Regional-indicator emoji are generated from the ISO country code.
COUNTRIES = [
    ("AF", "Afghanistan"),
    ("AL", "Albania"),
    ("DZ", "Algeria"),
    ("AD", "Andorra"),
    ("AO", "Angola"),
    ("AG", "Antigua and Barbuda"),
    ("AR", "Argentina"),
    ("AM", "Armenia"),
    ("AU", "Australia"),
    ("AT", "Austria"),
    ("AZ", "Azerbaijan"),
    ("BS", "Bahamas"),
    ("BH", "Bahrain"),
    ("BD", "Bangladesh"),
    ("BB", "Barbados"),
    ("BY", "Belarus"),
    ("BE", "Belgium"),
    ("BZ", "Belize"),
    ("BJ", "Benin"),
    ("BT", "Bhutan"),
    ("BO", "Bolivia"),
    ("BA", "Bosnia and Herzegovina"),
    ("BW", "Botswana"),
    ("BR", "Brazil"),
    ("BN", "Brunei"),
    ("BG", "Bulgaria"),
    ("BF", "Burkina Faso"),
    ("BI", "Burundi"),
    ("CV", "Cabo Verde"),
    ("KH", "Cambodia"),
    ("CM", "Cameroon"),
    ("CA", "Canada"),
    ("CF", "Central African Republic"),
    ("TD", "Chad"),
    ("CL", "Chile"),
    ("CN", "China"),
    ("CO", "Colombia"),
    ("KM", "Comoros"),
    ("CG", "Congo"),
    ("CR", "Costa Rica"),
    ("CI", "Côte d’Ivoire"),
    ("HR", "Croatia"),
    ("CU", "Cuba"),
    ("CY", "Cyprus"),
    ("CZ", "Czechia"),
    ("CD", "Democratic Republic of the Congo"),
    ("DK", "Denmark"),
    ("DJ", "Djibouti"),
    ("DM", "Dominica"),
    ("DO", "Dominican Republic"),
    ("EC", "Ecuador"),
    ("EG", "Egypt"),
    ("SV", "El Salvador"),
    ("GQ", "Equatorial Guinea"),
    ("ER", "Eritrea"),
    ("EE", "Estonia"),
    ("SZ", "Eswatini"),
    ("ET", "Ethiopia"),
    ("FJ", "Fiji"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("GA", "Gabon"),
    ("GM", "Gambia"),
    ("GE", "Georgia"),
    ("DE", "Germany"),
    ("GH", "Ghana"),
    ("GR", "Greece"),
    ("GD", "Grenada"),
    ("GT", "Guatemala"),
    ("GN", "Guinea"),
    ("GW", "Guinea-Bissau"),
    ("GY", "Guyana"),
    ("HT", "Haiti"),
    ("HN", "Honduras"),
    ("HU", "Hungary"),
    ("IS", "Iceland"),
    ("IN", "India"),
    ("ID", "Indonesia"),
    ("IR", "Iran"),
    ("IQ", "Iraq"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IT", "Italy"),
    ("JM", "Jamaica"),
    ("JP", "Japan"),
    ("JO", "Jordan"),
    ("KZ", "Kazakhstan"),
    ("KE", "Kenya"),
    ("KI", "Kiribati"),
    ("KW", "Kuwait"),
    ("KG", "Kyrgyzstan"),
    ("LA", "Laos"),
    ("LV", "Latvia"),
    ("LB", "Lebanon"),
    ("LS", "Lesotho"),
    ("LR", "Liberia"),
    ("LY", "Libya"),
    ("LI", "Liechtenstein"),
    ("LT", "Lithuania"),
    ("LU", "Luxembourg"),
    ("MG", "Madagascar"),
    ("MW", "Malawi"),
    ("MY", "Malaysia"),
    ("MV", "Maldives"),
    ("ML", "Mali"),
    ("MT", "Malta"),
    ("MH", "Marshall Islands"),
    ("MR", "Mauritania"),
    ("MU", "Mauritius"),
    ("MX", "Mexico"),
    ("FM", "Micronesia"),
    ("MD", "Moldova"),
    ("MC", "Monaco"),
    ("MN", "Mongolia"),
    ("ME", "Montenegro"),
    ("MA", "Morocco"),
    ("MZ", "Mozambique"),
    ("MM", "Myanmar"),
    ("NA", "Namibia"),
    ("NR", "Nauru"),
    ("NP", "Nepal"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("NI", "Nicaragua"),
    ("NE", "Niger"),
    ("NG", "Nigeria"),
    ("KP", "North Korea"),
    ("MK", "North Macedonia"),
    ("NO", "Norway"),
    ("OM", "Oman"),
    ("PK", "Pakistan"),
    ("PW", "Palau"),
    ("PS", "Palestine"),
    ("PA", "Panama"),
    ("PG", "Papua New Guinea"),
    ("PY", "Paraguay"),
    ("PE", "Peru"),
    ("PH", "Philippines"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("QA", "Qatar"),
    ("RO", "Romania"),
    ("RU", "Russia"),
    ("RW", "Rwanda"),
    ("KN", "Saint Kitts and Nevis"),
    ("LC", "Saint Lucia"),
    ("VC", "Saint Vincent and the Grenadines"),
    ("WS", "Samoa"),
    ("SM", "San Marino"),
    ("ST", "Sao Tome and Principe"),
    ("SA", "Saudi Arabia"),
    ("SN", "Senegal"),
    ("RS", "Serbia"),
    ("SC", "Seychelles"),
    ("SL", "Sierra Leone"),
    ("SG", "Singapore"),
    ("SK", "Slovakia"),
    ("SI", "Slovenia"),
    ("SB", "Solomon Islands"),
    ("SO", "Somalia"),
    ("ZA", "South Africa"),
    ("KR", "South Korea"),
    ("SS", "South Sudan"),
    ("ES", "Spain"),
    ("LK", "Sri Lanka"),
    ("SD", "Sudan"),
    ("SR", "Suriname"),
    ("SE", "Sweden"),
    ("CH", "Switzerland"),
    ("SY", "Syria"),
    ("TJ", "Tajikistan"),
    ("TZ", "Tanzania"),
    ("TH", "Thailand"),
    ("TL", "Timor-Leste"),
    ("TG", "Togo"),
    ("TO", "Tonga"),
    ("TT", "Trinidad and Tobago"),
    ("TN", "Tunisia"),
    ("TR", "Türkiye"),
    ("TM", "Turkmenistan"),
    ("TV", "Tuvalu"),
    ("UG", "Uganda"),
    ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom"),
    ("US", "United States"),
    ("UY", "Uruguay"),
    ("UZ", "Uzbekistan"),
    ("VU", "Vanuatu"),
    ("VA", "Vatican City"),
    ("VE", "Venezuela"),
    ("VN", "Vietnam"),
    ("YE", "Yemen"),
    ("ZM", "Zambia"),
    ("ZW", "Zimbabwe"),
]
COUNTRY_ALIASES = {
    "United States": ["USA", "US", "America"],
    "United Kingdom": ["UK", "Great Britain", "Britain"],
    "South Korea": ["Republic of Korea"],
    "North Korea": ["DPRK", "Democratic People's Republic of Korea"],
    "Russia": ["Russian Federation"],
    "Czechia": ["Czech Republic"],
    "Türkiye": ["Turkey"],
    "Côte d’Ivoire": ["Ivory Coast"],
    "Cabo Verde": ["Cape Verde"],
    "Eswatini": ["Swaziland"],
    "Türkiye": ["Turkey"],
    "Democratic Republic of the Congo": ["DR Congo", "DRC", "Congo-Kinshasa"],
    "Congo": ["Republic of the Congo", "Congo-Brazzaville"],
    "Palestine": ["State of Palestine"],
    "Vatican City": ["Holy See"],
}
def _flag_emoji(code: str) -> str:
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)

FLAGS = [
    (_flag_emoji(code), name, [name.casefold(), *[alias.casefold() for alias in COUNTRY_ALIASES.get(name, [])]])
    for code, name in COUNTRIES
]

DINO_QUESTIONS = [
    ("I am a huge semi-aquatic ambush predator with powerful jaws. Which Evrima species am I?", "Deinosuchus", ["deinosuchus", "deino"], "Ich bin ein riesiges semi-aquatisches Lauerraubtier mit kräftigen Kiefern. Welche Evrima-Art bin ich?", "Deinosuchus"),
    ("I am a small agile herbivore that uses powerful headbutts. Which species am I?", "Pachycephalosaurus", ["pachycephalosaurus", "pachy"], "Ich bin ein kleiner, wendiger Pflanzenfresser mit kräftigen Kopfstößen. Welche Art bin ich?", "Pachycephalosaurus"),
    ("I am a large herbivore with a long tail and strong defensive kicks. Which species am I?", "Tenontosaurus", ["tenontosaurus", "teno"], "Ich bin ein großer Pflanzenfresser mit langem Schwanz und kräftigen Verteidigungstritten. Welche Art bin ich?", "Tenontosaurus"),
    ("I am a fast carnivore with two horns above my eyes. Which species am I?", "Carnotaurus", ["carnotaurus", "carno"], "Ich bin ein schneller Fleischfresser mit zwei Hörnern über den Augen. Welche Art bin ich?", "Carnotaurus"),
    ("I am a robust carnivore known for a dangerous bite and bacteria mechanics. Which species am I?", "Ceratosaurus", ["ceratosaurus", "cera"], "Ich bin ein kräftiger Fleischfresser mit gefährlichem Biss und Bakterienmechanik. Welche Art bin ich?", "Ceratosaurus"),
    ("I am a large herbivore with back plates and a spiked tail. Which species am I?", "Stegosaurus", ["stegosaurus", "stego"], "Ich bin ein großer Pflanzenfresser mit Rückenplatten und Stachelschwanz. Welche Art bin ich?", "Stegosaurus"),
    ("I am a massive horned herbivore with a powerful charge. Which species am I?", "Triceratops", ["triceratops", "trike", "trike"], "Ich bin ein massiver gehör­nter Pflanzenfresser mit einem kräftigen Sturmangriff. Welche Art bin ich?", "Triceratops"),
    ("I am a large predator with a famous powerful bite. Which species am I?", "Tyrannosaurus", ["tyrannosaurus", "tyrannosaurus rex", "rex", "t rex", "trex"], "Ich bin ein großer Räuber mit einem berühmten kräftigen Biss. Welche Art bin ich?", "Tyrannosaurus"),
    ("I am a quick, small carnivore that hunts with venomous bites. Which species am I?", "Troodon", ["troodon"], "Ich bin ein kleiner, schneller Fleischfresser, der mit giftigen Bissen jagt. Welche Art bin ich?", "Troodon"),
    ("I am a gliding reptile with a long beak and wings. Which species am I?", "Pteranodon", ["pteranodon", "ptera"], "Ich bin ein fliegendes Reptil mit langem Schnabel und Flügeln. Welche Art bin ich?", "Pteranodon"),
    ("I am a small, fast herbivore that can sprint away from danger. Which species am I?", "Dryosaurus", ["dryosaurus", "dryo"], "Ich bin ein kleiner, schneller Pflanzenfresser, der vor Gefahr fliehen kann. Welche Art bin ich?", "Dryosaurus"),
    ("I am a fast omnivore known for running on two legs. Which species am I?", "Gallimimus", ["gallimimus", "galli"], "Ich bin ein schneller Allesfresser, der auf zwei Beinen läuft. Welche Art bin ich?", "Gallimimus"),
    ("I am a small carnivore that can climb and ambush prey. Which species am I?", "Herrerasaurus", ["herrerasaurus", "herra"], "Ich bin ein kleiner Fleischfresser, der klettern und Beute überraschen kann. Welche Art bin ich?", "Herrerasaurus"),
    ("I am a small herbivore with a distinctive head crest. Which species am I?", "Hypsilophodon", ["hypsilophodon", "hypsi"], "Ich bin ein kleiner Pflanzenfresser mit einem auffälligen Kopfkamm. Welche Art bin ich?", "Hypsilophodon"),
    ("I am an armored herbivore with a spiked tail. Which species am I?", "Kentrosaurus", ["kentrosaurus", "kentro"], "Ich bin ein gepanzerter Pflanzenfresser mit Stachelschwanz. Welche Art bin ich?", "Kentrosaurus"),
    ("I am a large herd herbivore known for strong defensive abilities. Which species am I?", "Maiasaura", ["maiasaura", "maia"], "Ich bin ein großer Herden-Pflanzenfresser mit starken Verteidigungsfähigkeiten. Welche Art bin ich?", "Maiasaura"),
    ("I am a small pouncing carnivore also known as Omniraptor. Which species am I?", "Omniraptor", ["omniraptor", "omni"], "Ich bin ein kleiner springender Fleischfresser, auch Omniraptor genannt. Welche Art bin ich?", "Omniraptor"),
    ("I am a small semi-aquatic omnivore with feather-like traits. Which species am I?", "Beipiaosaurus", ["beipiaosaurus", "beipi"], "Ich bin ein kleiner semi-aquatischer Allesfresser mit federartigen Merkmalen. Welche Art bin ich?", "Beipiaosaurus"),
    ("I am a large horned herbivore with a defensive frill. Which species am I?", "Diabloceratops", ["diabloceratops", "diablo"], "Ich bin ein großer gehör­nter Pflanzenfresser mit Nackenschild. Welche Art bin ich?", "Diabloceratops"),
    ("I am a medium-sized carnivore with two crests on my head. Which species am I?", "Dilophosaurus", ["dilophosaurus", "dilo"], "Ich bin ein mittelgroßer Fleischfresser mit zwei Kämmen auf dem Kopf. Welche Art bin ich?", "Dilophosaurus"),
    ("I am a small aquatic-capable carnivore with a long snout. Which species am I?", "Austroraptor", ["austroraptor", "austro"], "Ich bin ein kleiner Fleischfresser mit langer Schnauze, der auch im Wasser zurechtkommt. Welche Art bin ich?", "Austroraptor"),
    ("I am a large bipedal carnivore with three claws on each hand. Which species am I?", "Allosaurus", ["allosaurus", "allo"], "Ich bin ein großer zweibeiniger Fleischfresser mit drei Krallen an jeder Hand. Welche Art bin ich?", "Allosaurus"),
]

DINO_TRANSLATIONS = {
    "fr": [
        "Je suis un grand prédateur semi-aquatique qui chasse à l’affût. Quel dinosaure d’Evrima suis-je ?",
        "Je suis un petit herbivore agile qui utilise ses puissantes pattes arrière. Quel dinosaure suis-je ?",
        "Je suis un grand herbivore avec une longue queue et des pattes fortes. Quel dinosaure suis-je ?",
        "Je suis un grand carnivore avec des cornes au-dessus des yeux. Quel dinosaure suis-je ?",
        "Je suis un carnivore robuste doté d’une morsure dangereuse. Quel dinosaure suis-je ?",
        "Je suis un grand herbivore avec des plaques sur le dos et une queue épineuse. Quel dinosaure suis-je ?",
    ],
    "es": [
        "Soy un gran depredador semiacuático que caza por emboscada. ¿Qué dinosaurio de Evrima soy?",
        "Soy un pequeño herbívoro ágil que usa sus fuertes patas traseras. ¿Qué dinosaurio soy?",
        "Soy un gran herbívoro con una cola larga y patas fuertes. ¿Qué dinosaurio soy?",
        "Soy un gran carnívoro con cuernos sobre los ojos. ¿Qué dinosaurio soy?",
        "Soy un carnívoro robusto con una mordida peligrosa. ¿Qué dinosaurio soy?",
        "Soy un gran herbívoro con placas en el lomo y una cola con púas. ¿Qué dinosaurio soy?",
    ],
    "it": [
        "Sono un grande predatore semiacquatico che caccia di sorpresa. Quale dinosauro di Evrima sono?",
        "Sono un piccolo erbivoro agile che usa le sue potenti zampe posteriori. Quale dinosauro sono?",
        "Sono un grande erbivoro con una lunga coda e zampe forti. Quale dinosauro sono?",
        "Sono un grande carnivoro con corna sopra gli occhi. Quale dinosauro sono?",
        "Sono un carnivoro robusto con un morso pericoloso. Quale dinosauro sono?",
        "Sono un grande erbivoro con placche sul dorso e una coda spinosa. Quale dinosauro sono?",
    ],
    "pt": [
        "Sou um grande predador semiaquático que caça de emboscada. Que dinossauro de Evrima sou eu?",
        "Sou um pequeno herbívoro ágil que usa as poderosas patas traseiras. Que dinossauro sou eu?",
        "Sou um grande herbívoro com cauda longa e patas fortes. Que dinossauro sou eu?",
        "Sou um grande carnívoro com chifres acima dos olhos. Que dinossauro sou eu?",
        "Sou um carnívoro robusto com uma mordida perigosa. Que dinossauro sou eu?",
        "Sou um grande herbívoro com placas nas costas e uma cauda com espinhos. Que dinossauro sou eu?",
    ],
    "nl": [
        "Ik ben een groot semi-aquatisch roofdier dat vanuit een hinderlaag aanvalt. Welke Evrima-dinosaurus ben ik?",
        "Ik ben een kleine, behendige planteneter die mijn krachtige achterpoten gebruikt. Welke dinosaurus ben ik?",
        "Ik ben een grote planteneter met een lange staart en sterke poten. Welke dinosaurus ben ik?",
        "Ik ben een grote vleeseter met hoorns boven mijn ogen. Welke dinosaurus ben ik?",
        "Ik ben een stevig gebouwde vleeseter met een gevaarlijke beet. Welke dinosaurus ben ik?",
        "Ik ben een grote planteneter met platen op mijn rug en een stekelige staart. Welke dinosaurus ben ik?",
    ],
    "pl": [
        "Jestem dużym półwodnym drapieżnikiem polującym z zasadzki. Jakim dinozaurem z Evrimy jestem?",
        "Jestem małym, zwinnym roślinożercą używającym silnych tylnych nóg. Jakim dinozaurem jestem?",
        "Jestem dużym roślinożercą z długim ogonem i silnymi nogami. Jakim dinozaurem jestem?",
        "Jestem dużym mięsożercą z rogami nad oczami. Jakim dinozaurem jestem?",
        "Jestem masywnym mięsożercą z niebezpiecznym ugryzieniem. Jakim dinozaurem jestem?",
        "Jestem dużym roślinożercą z płytami na grzbiecie i kolczastym ogonem. Jakim dinozaurem jestem?",
    ],
    "ru": [
        "Я крупный полуводный хищник, охотящийся из засады. Какой я динозавр из Evrima?",
        "Я небольшой и ловкий травоядный динозавр с мощными задними лапами. Кто я?",
        "Я крупный травоядный динозавр с длинным хвостом и сильными ногами. Кто я?",
        "Я крупный хищник с рогами над глазами. Кто я?",
        "Я крепкий хищник с опасным укусом. Кто я?",
        "Я крупный травоядный динозавр с пластинами на спине и шипастым хвостом. Кто я?",
    ],
}

_TRANSLATION_CACHE = {}

async def _translate_text(text: str, language: str) -> str:
    """Translate one game prompt/choice, caching successful results."""
    if not text or language == "en":
        return text
    key = (language, text)
    if key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[key]
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "en", "tl": language, "dt": "t", "q": text},
            ) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        translated = "".join(part[0] for part in (payload[0] or []) if isinstance(part, list) and part and part[0]).strip()
        if translated:
            _TRANSLATION_CACHE[key] = translated
            return translated
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, IndexError) as exc:
        print(f"[MiniGames] Translation failed ({language}): {exc}")
    return text

async def _translate_texts(texts: list[str], language: str) -> list[str]:
    return list(await asyncio.gather(*(_translate_text(text, language) for text in texts)))

def _dino_question(question_en: str, question_de: str, language: str) -> str:
    if language == "de":
        return question_de
    if language == "en":
        return question_en
    index = next((i for i, item in enumerate(DINO_QUESTIONS) if item[0] == question_en), None)
    translations = DINO_TRANSLATIONS.get(language, [])
    return translations[index] if index is not None and index < len(translations) else question_en


QUIZ_TRANSLATIONS = {
    "de": [
        ("Welcher Planet ist als der Rote Planet bekannt?", ["Mars", "Venus", "Jupiter", "Merkur"]),
        ("Welches Material wird in Minecraft üblicherweise für eine Werkbank verwendet?", ["Holzbretter", "Eisenbarren", "Bruchstein", "Glas"]),
        ("Wofür steht CPU?", ["Zentrale Verarbeitungseinheit", "Computer-Stromversorgung", "Kernprogramm-Upload", "Zentrale Leistungssteuerung"]),
        ("Wie viele Seiten hat ein Sechseck?", ["Fünf", "Sechs", "Sieben", "Acht"]),
        ("Welcher Ozean ist der größte der Erde?", ["Atlantik", "Indischer Ozean", "Arktischer Ozean", "Pazifik"]),
        ("Welche Schachfigur zieht in einer L-Form?", ["Läufer", "Springer", "Turm", "Dame"]),
        ("Was ist das chemische Symbol für Gold?", ["Ag", "Gd", "Au", "Go"]),
        ("Welche Spielreihe enthält den Ort Los Santos?", ["Grand Theft Auto", "Die Sims", "Forza Horizon", "Watch Dogs"]),
        ("Wie viele Spieler hat eine Fußballmannschaft während des normalen Spiels auf dem Feld?", ["9", "10", "11", "12"]),
        ("Was ist das größte Säugetier der Erde?", ["Afrikanischer Elefant", "Blauwal", "Giraffe", "Orca"]),
    ],
    "fr": [
        ("Quelle planète est connue comme la planète rouge ?", ["Mars", "Vénus", "Jupiter", "Mercure"]),
        ("Dans Minecraft, quel matériau sert généralement à fabriquer une table de craft ?", ["Planches de bois", "Lingots de fer", "Pierres taillées", "Verre"]),
        ("Que signifie CPU ?", ["Unité centrale de traitement", "Utilitaire d’alimentation informatique", "Téléversement de programme central", "Utilisateur de performance centrale"]),
        ("Combien de côtés possède un hexagone ?", ["Cinq", "Six", "Sept", "Huit"]),
        ("Quel est le plus grand océan de la Terre ?", ["Atlantique", "Indien", "Arctique", "Pacifique"]),
        ("Aux échecs, quelle pièce se déplace en forme de L ?", ["Fou", "Cavalier", "Tour", "Dame"]),
        ("Quel est le symbole chimique de l’or ?", ["Ag", "Gd", "Au", "Go"]),
        ("Quelle série de jeux comprend le lieu appelé Los Santos ?", ["Grand Theft Auto", "Les Sims", "Forza Horizon", "Watch Dogs"]),
        ("Combien de joueurs d’une équipe de football sont sur le terrain en jeu normal ?", ["9", "10", "11", "12"]),
        ("Quel est le plus grand mammifère sur Terre ?", ["Éléphant d’Afrique", "Baleine bleue", "Girafe", "Orque"]),
    ],
    "es": [
        ("¿Qué planeta es conocido como el planeta rojo?", ["Marte", "Venus", "Júpiter", "Mercurio"]),
        ("En Minecraft, ¿qué material se usa normalmente para fabricar una mesa de trabajo?", ["Tablones de madera", "Lingotes de hierro", "Piedra labrada", "Cristal"]),
        ("¿Qué significa CPU?", ["Unidad central de procesamiento", "Utilidad de energía informática", "Carga de programa central", "Usuario de rendimiento central"]),
        ("¿Cuántos lados tiene un hexágono?", ["Cinco", "Seis", "Siete", "Ocho"]),
        ("¿Cuál es el océano más grande de la Tierra?", ["Atlántico", "Índico", "Ártico", "Pacífico"]),
        ("En ajedrez, ¿qué pieza se mueve en forma de L?", ["Alfil", "Caballo", "Torre", "Dama"]),
        ("¿Cuál es el símbolo químico del oro?", ["Ag", "Gd", "Au", "Go"]),
        ("¿Qué saga de videojuegos incluye el lugar llamado Los Santos?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"]),
        ("¿Cuántos jugadores de un equipo de fútbol están en el campo durante el juego normal?", ["9", "10", "11", "12"]),
        ("¿Cuál es el mamífero más grande de la Tierra?", ["Elefante africano", "Ballena azul", "Jirafa", "Orca"]),
    ],
    "it": [
        ("Quale pianeta è conosciuto come il Pianeta Rosso?", ["Marte", "Venere", "Giove", "Mercurio"]),
        ("In Minecraft, quale materiale si usa comunemente per creare un banco da lavoro?", ["Assi di legno", "Lingotti di ferro", "Pietrisco", "Vetro"]),
        ("Che cosa significa CPU?", ["Unità centrale di elaborazione", "Utilità di alimentazione del computer", "Caricamento del programma centrale", "Utente delle prestazioni centrali"]),
        ("Quanti lati ha un esagono?", ["Cinque", "Sei", "Sette", "Otto"]),
        ("Qual è l'oceano più grande della Terra?", ["Atlantico", "Indiano", "Artico", "Pacifico"]),
        ("Negli scacchi, quale pezzo si muove a L?", ["Alfiere", "Cavallo", "Torre", "Regina"]),
        ("Qual è il simbolo chimico dell'oro?", ["Ag", "Gd", "Au", "Go"]),
        ("Quale serie di giochi include il luogo chiamato Los Santos?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"]),
        ("Quanti giocatori di una squadra di calcio sono in campo durante il gioco normale?", ["9", "10", "11", "12"]),
        ("Qual è il mammifero più grande della Terra?", ["Elefante africano", "Balena azzurra", "Giraffa", "Orca"]),
    ],
    "pt": [
        ("Qual planeta é conhecido como Planeta Vermelho?", ["Marte", "Vênus", "Júpiter", "Mercúrio"]),
        ("No Minecraft, qual material é normalmente usado para criar uma mesa de trabalho?", ["Tábuas de madeira", "Barras de ferro", "Pedregulho", "Vidro"]),
        ("O que significa CPU?", ["Unidade central de processamento", "Utilitário de energia do computador", "Envio de programa central", "Usuário de desempenho central"]),
        ("Quantos lados tem um hexágono?", ["Cinco", "Seis", "Sete", "Oito"]),
        ("Qual é o maior oceano da Terra?", ["Atlântico", "Índico", "Ártico", "Pacífico"]),
        ("No xadrez, qual peça se move em forma de L?", ["Bispo", "Cavalo", "Torre", "Dama"]),
        ("Qual é o símbolo químico do ouro?", ["Ag", "Gd", "Au", "Go"]),
        ("Qual série de jogos apresenta o local chamado Los Santos?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"]),
        ("Quantos jogadores de uma equipe de futebol ficam em campo durante o jogo normal?", ["9", "10", "11", "12"]),
        ("Qual é o maior mamífero da Terra?", ["Elefante-africano", "Baleia-azul", "Girafa", "Orca"]),
    ],
    "nl": [
        ("Welke planeet staat bekend als de Rode Planeet?", ["Mars", "Venus", "Jupiter", "Mercurius"]),
        ("Welk materiaal wordt in Minecraft meestal gebruikt om een werkbank te maken?", ["Houten planken", "IJzerstaven", "Keien", "Glas"]),
        ("Waar staat CPU voor?", ["Central Processing Unit", "Computer Power Utility", "Core Program Upload", "Central Performance User"]),
        ("Hoeveel zijden heeft een zeshoek?", ["Vijf", "Zes", "Zeven", "Acht"]),
        ("Wat is de grootste oceaan op aarde?", ["Atlantische Oceaan", "Indische Oceaan", "Arctische Oceaan", "Stille Oceaan"]),
        ("Welk schaakstuk beweegt in een L-vorm?", ["Loper", "Paard", "Toren", "Dame"]),
        ("Wat is het chemische symbool voor goud?", ["Ag", "Gd", "Au", "Go"]),
        ("Welke gameserie bevat de locatie Los Santos?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"]),
        ("Hoeveel spelers van één voetbalteam staan tijdens normaal spel op het veld?", ["9", "10", "11", "12"]),
        ("Wat is het grootste zoogdier op aarde?", ["Afrikaanse olifant", "Blauwe vinvis", "Giraf", "Orka"]),
    ],
    "pl": [
        ("Która planeta jest znana jako Czerwona Planeta?", ["Mars", "Wenus", "Jowisz", "Merkury"]),
        ("Jaki materiał jest zwykle używany w Minecraft do stworzenia stołu rzemieślniczego?", ["Deski", "Sztabki żelaza", "Bruk", "Szkło"]),
        ("Co oznacza skrót CPU?", ["Centralna jednostka przetwarzająca", "Komputerowe zasilanie użytkowe", "Przesyłanie programu głównego", "Centralny użytkownik wydajności"]),
        ("Ile boków ma sześciokąt?", ["Pięć", "Sześć", "Siedem", "Osiem"]),
        ("Który ocean jest największy na Ziemi?", ["Atlantycki", "Indyjski", "Arktyczny", "Spokojny"]),
        ("Która figura szachowa porusza się w kształcie litery L?", ["Goniec", "Skoczek", "Wieża", "Hetman"]),
        ("Jaki jest symbol chemiczny złota?", ["Ag", "Gd", "Au", "Go"]),
        ("Która seria gier zawiera miejsce o nazwie Los Santos?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"]),
        ("Ilu zawodników jednej drużyny piłkarskiej jest na boisku podczas normalnej gry?", ["9", "10", "11", "12"]),
        ("Jaki jest największy ssak na Ziemi?", ["Słoń afrykański", "Płetwal błękitny", "Żyrafa", "Orka"]),
    ],
    "ru": [
        ("Какая планета известна как Красная планета?", ["Марс", "Венера", "Юпитер", "Меркурий"]),
        ("Какой материал обычно используют в Minecraft для создания верстака?", ["Деревянные доски", "Железные слитки", "Булыжник", "Стекло"]),
        ("Что означает CPU?", ["Центральный процессор", "Утилита питания компьютера", "Загрузка центральной программы", "Центральный пользователь производительности"]),
        ("Сколько сторон у шестиугольника?", ["Пять", "Шесть", "Семь", "Восемь"]),
        ("Какой океан самый большой на Земле?", ["Атлантический", "Индийский", "Северный Ледовитый", "Тихий"]),
        ("Какая шахматная фигура ходит буквой «Г»?", ["Слон", "Конь", "Ладья", "Ферзь"]),
        ("Какой химический символ у золота?", ["Ag", "Gd", "Au", "Go"]),
        ("В какой серии игр есть место под названием Лос-Сантос?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"]),
        ("Сколько игроков одной футбольной команды находится на поле во время обычной игры?", ["9", "10", "11", "12"]),
        ("Какое млекопитающее самое большое на Земле?", ["Африканский слон", "Синий кит", "Жираф", "Косатка"]),
    ],
}

QUIZ_QUESTIONS = [
    ("Which planet is known as the Red Planet?", ["Mars", "Venus", "Jupiter", "Mercury"], 0),
    ("In Minecraft, which material is commonly used to craft a crafting table?", ["Wood planks", "Iron ingots", "Cobblestone", "Glass"], 0),
    ("What does CPU stand for?", ["Central Processing Unit", "Computer Power Utility", "Core Program Upload", "Central Performance User"], 0),
    ("How many sides does a hexagon have?", ["Five", "Six", "Seven", "Eight"], 1),
    ("Which ocean is the largest on Earth?", ["Atlantic", "Indian", "Arctic", "Pacific"], 3),
    ("In chess, which piece moves in an L-shape?", ["Bishop", "Knight", "Rook", "Queen"], 1),
    ("What is the chemical symbol for gold?", ["Ag", "Gd", "Au", "Go"], 2),
    ("Which game series features the location called Los Santos?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"], 0),
    ("How many players are on the field for one soccer team during normal play?", ["9", "10", "11", "12"], 2),
    ("What is the largest mammal on Earth?", ["African elephant", "Blue whale", "Giraffe", "Orca"], 1),
    ("How many players does one soccer team normally have on the field?", ["9", "10", "11", "12"], 2),
    ("Which animal is known as the fastest land animal?", ["Lion", "Cheetah", "Horse", "Leopard"], 1),
    ("What is the largest hot desert in the world?", ["Gobi", "Sahara", "Kalahari", "Arabian"], 1),
    ("Which country is home to the Great Pyramid of Giza?", ["Greece", "Mexico", "Egypt", "India"], 2),
    ("How many sides does a triangle have?", ["Two", "Three", "Four", "Five"], 1),
    ("Which instrument has black and white keys?", ["Violin", "Piano", "Flute", "Drum"], 1),
    ("What is the freezing point of water in Celsius?", ["0", "10", "32", "100"], 0),
    ("Which language is primarily spoken in Brazil?", ["Spanish", "Portuguese", "French", "English"], 1),
    ("Which is the smallest prime number?", ["0", "1", "2", "3"], 2),
    ("What is the capital of Australia?", ["Sydney", "Melbourne", "Canberra", "Perth"], 2),
    ("Which planet has the most prominent ring system?", ["Mars", "Saturn", "Venus", "Mercury"], 1),
    ("What is the largest organ of the human body?", ["Heart", "Liver", "Skin", "Lung"], 2),
    ("How many minutes are in two hours?", ["60", "90", "120", "180"], 2),
    ("Which metal is liquid at ordinary room temperature?", ["Iron", "Mercury", "Copper", "Aluminium"], 1),
    ("Which ocean lies between Africa and Australia?", ["Atlantic", "Pacific", "Indian", "Arctic"], 2),
    ("What is the currency of the United Kingdom?", ["Euro", "Pound sterling", "Dollar", "Franc"], 1),
    ("Which bird is famous for its ability to mimic human speech?", ["Eagle", "Parrot", "Penguin", "Ostrich"], 1),
    ("How many degrees are in a right angle?", ["45", "60", "90", "180"], 2),
    ("Which country gifted the Statue of Liberty to the United States?", ["France", "Italy", "Spain", "Canada"], 0),
    ("What is the hardest naturally occurring mineral?", ["Quartz", "Diamond", "Topaz", "Granite"], 1),
    ("Which blood cells help the body fight infections?", ["Red cells", "White cells", "Platelets", "Plasma"], 1),
    ("What is the main star at the center of our solar system?", ["Sirius", "Polaris", "The Sun", "Vega"], 2),
    ("Which animal is the tallest living land animal?", ["Elephant", "Giraffe", "Ostrich", "Camel"], 1),
    ("How many colors are traditionally named in a rainbow?", ["Five", "Six", "Seven", "Eight"], 2),
    ("Which continent contains the Amazon rainforest?", ["Africa", "Asia", "South America", "Europe"], 2),
    ("What is the capital of Canada?", ["Toronto", "Vancouver", "Ottawa", "Montreal"], 2),
    ("Which force pulls objects toward Earth?", ["Magnetism", "Gravity", "Friction", "Pressure"], 1),
    ("Which chess piece moves in an L-shape?", ["Bishop", "Knight", "Rook", "Queen"], 1),
    ("What does CPU stand for?", ["Central Processing Unit", "Computer Power Utility", "Core Program Upload", "Central Performance User"], 0),
    ("Which game series features Los Santos?", ["Grand Theft Auto", "The Sims", "Forza Horizon", "Watch Dogs"], 0),
    ("Which planet is famous for its Great Red Spot?", ["Jupiter", "Neptune", "Earth", "Mars"], 0),
    ("What is the largest species of big cat?", ["Lion", "Tiger", "Leopard", "Jaguar"], 1),
    ("Which country has the maple leaf on its flag?", ["Canada", "Austria", "Denmark", "Finland"], 0),
    ("How many bones are in a typical adult human body?", ["106", "206", "306", "406"], 1),
    ("Which is the longest river in South America?", ["Nile", "Amazon", "Danube", "Yangtze"], 1),
    ("What is the square root of 144?", ["10", "11", "12", "14"], 2),
    ("Which device measures temperature?", ["Barometer", "Thermometer", "Altimeter", "Compass"], 1),
    ("Which sport uses a shuttlecock?", ["Tennis", "Badminton", "Squash", "Table tennis"], 1),
    ("What is the capital of Italy?", ["Milan", "Venice", "Rome", "Naples"], 2),
    ("Which element has the chemical symbol O?", ["Gold", "Oxygen", "Osmium", "Iron"], 1),
    ("Which ocean is the smallest?", ["Indian", "Arctic", "Atlantic", "Pacific"], 1),
    ("What is the process by which plants make food using sunlight?", ["Respiration", "Photosynthesis", "Fermentation", "Digestion"], 1),
    ("How many days are in a leap year?", ["364", "365", "366", "367"], 2),
    ("Which country is famous for the ancient city of Petra?", ["Jordan", "Egypt", "Turkey", "Greece"], 0),
    ("Which animal is a mammal that lays eggs?", ["Dolphin", "Platypus", "Koala", "Panda"], 1),
    ("What is the largest planet in our solar system?", ["Earth", "Saturn", "Jupiter", "Neptune"], 2),
    ("Which shape has four equal sides and four right angles?", ["Rectangle", "Rhombus", "Square", "Trapezoid"], 2),
    ("What is the capital of Spain?", ["Barcelona", "Madrid", "Seville", "Valencia"], 1),
    ("Which natural satellite orbits Earth?", ["The Moon", "Phobos", "Europa", "Titan"], 0),
    ("What is the boiling point of water at sea level in Celsius?", ["50", "75", "100", "120"], 2),
]

DINO_HINTS = {
    "Deinosuchus": [
        "This animal spends much of its time in or near water.",
        "It is a patient ambush hunter rather than a fast runner.",
        "It can threaten prey at the water's edge.",
        "Its body is built for swimming and powerful lunges.",
        "It is a crocodilian, not a true dinosaur.",
        "Its name begins with the letter D.",
        "It is one of the largest aquatic predators in Evrima.",
        "Its short name is often written as 'deino'.",
        "The name has four syllables: Dei-no-su-chus.",
        "The answer starts with 'Deino'.",
    ],
    "Pachycephalosaurus": [
        "This dinosaur is a herbivore.",
        "It is smaller and more agile than the large heavyweight herbivores.",
        "It relies on speed and movement to survive.",
        "Its strongest feature is associated with its head and legs.",
        "Its name is often shortened by players.",
        "Its short name begins with 'P'.",
        "It is known for head-first impacts.",
        "The full name is unusually long.",
        "Its common short name is 'pachy'.",
        "The answer begins with 'Pachycephalo'.",
    ],
    "Tenontosaurus": [
        "This dinosaur eats plants.",
        "It is built for moving on land rather than living in water.",
        "It has a long tail that helps with balance.",
        "Its body is sturdy and its legs are strong.",
        "Its name begins with the letter T.",
        "Players often shorten its name to five letters.",
        "It is not the horned carnivore in this question set.",
        "Its short name is 'teno'.",
        "The full name contains 'tenonto'.",
        "The answer begins with 'Tenonto'.",
    ],
    "Carnotaurus": [
        "This dinosaur is a carnivore.",
        "It has distinctive horns on its head.",
        "The horns are located above its eyes.",
        "It is known for a powerful, fast-running build.",
        "Its name begins with the letter C.",
        "Its short name is often used by players.",
        "The short name is 'carno'.",
        "It is not the aquatic ambush predator.",
        "The full name starts with 'Carno'.",
        "The answer is Carnotaurus.",
    ],
    "Ceratosaurus": [
        "This dinosaur is a carnivore.",
        "It has a robust body and a dangerous bite.",
        "Its name begins with the letter C, like another carnivore in the set.",
        "Its name is not Carnotaurus.",
        "Its short name begins with 'cera'.",
        "It is known for a prominent horn on its snout.",
        "It is a land-based predator.",
        "The short name is 'cera'.",
        "The full name begins with 'Cerato'.",
        "The answer is Ceratosaurus.",
    ],
    "Stegosaurus": [
        "This dinosaur is a herbivore.",
        "Its silhouette is easy to recognize from its back.",
        "It has large plates along its back.",
        "Its tail is equipped with spikes.",
        "Its name begins with the letter S.",
        "It is not the long-tailed Tenontosaurus.",
        "Its short name is 'stego'.",
        "The name begins with 'Stego'.",
        "It is famous for its back plates and spiked tail.",
        "The answer is Stegosaurus.",
    ],
}


def _next_quiz_question(asked: list[str]):
    """Choose a question not yet used in this round until the pool is exhausted."""
    available = [item for item in QUIZ_QUESTIONS if item[0] not in asked]
    if not available:
        available = list(QUIZ_QUESTIONS)
    return random.choice(available)


SCRAMBLE_WORDS = [
    "APPLE", "BANANA", "ORANGE", "ELEPHANT", "GIRAFFE", "PENGUIN",
    "CROCODILE", "BUTTERFLY", "MOUNTAIN", "WATERFALL", "JUNGLE",
    "ISLAND", "OCEAN", "FOREST", "RIVER", "THUNDER", "RAINBOW",
    "KEYBOARD", "COMPUTER", "HEADPHONES", "CHALLENGE", "VICTORY",
    "STRATEGY", "ADVENTURE", "MYSTERY", "GALAXY", "PLANET", "ASTEROID",
    "DINOSAUR", "CARNIVORE", "HERBIVORE", "PREDATOR", "HATCHLING",
    "STEGOSAURUS", "TRICERATOPS", "CARNOTAURUS", "DEINOSUCHUS",
    "TENONTOSAURUS", "PTERANODON", "OMNIRAPTOR", "GALLIMIMUS",
    "CERATOSAURUS", "DRYOSAURUS", "AUSTRORAPTOR", "HERRERASAURUS",
    "SURVIVAL", "HUNTER", "STAMINA", "MUTATION", "NUTRIENTS",
    "DISCORD", "TEAMWORK", "FRIENDSHIP", "KEYSTONE", "LIGHTNING",
]


def _scramble_word(word: str) -> str:
    """Shuffle letters and avoid returning the original word when possible."""
    letters = list(word)
    for _ in range(12):
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:
            return scrambled
    return word[::-1]


HANGMAN_WORDS = [
    "EVRIMA", "DINOSAUR", "CARNOTAURUS", "DEINOSUCHUS", "TENONTOSAURUS",
    "STEAM", "GAMING", "SURVIVAL", "HUNTER", "FOREST", "PACHY",
    "ALLOSAURUS", "AUSTRORAPTOR", "BEIPIAOSAURUS", "CERATOSAURUS",
    "DIABLOCERATOPS", "DILOPHOSAURUS", "DRYOSAURUS", "GALLIMIMUS",
    "HERRERASAURUS", "HYPSILOPHODON", "KENTROSAURUS", "MAIASAURA",
    "OMNIRAPTOR", "PTERANODON", "STEGOSAURUS", "TRICERATOPS",
    "TROODON", "TYRANNOSAURUS", "CARNIVORE", "HERBIVORE", "OMNIVORE",
    "PREDATOR", "PREY", "NEST", "HATCHLING", "GROWTH", "STAMINA",
    "BLEEDING", "MUTATION", "NUTRIENTS", "WATERFALL", "LAGOON",
    "RIVER", "SWAMP", "MOUNTAIN", "JUNGLE", "CAVE", "BEACH",
    "VOLCANO", "ISLAND", "OCEAN", "DESERT", "SAVANNA",
    "COMPUTER", "KEYBOARD", "MONITOR", "HEADSET", "MOUSE",
    "INTERNET", "DISCORD", "SERVER", "CHANNEL", "MESSAGE",
    "CHALLENGE", "VICTORY", "DEFEAT", "TEAMWORK", "STRATEGY",
    "GALAXY", "PLANET", "COMET", "ASTEROID", "NEBULA",
    "SATELLITE", "TELESCOPE", "ASTRONOMY", "GRAVITY", "ECLIPSE",
    "ELEPHANT", "GIRAFFE", "PENGUIN", "DOLPHIN", "CHEETAH",
    "CROCODILE", "BUTTERFLY", "OCTOPUS", "KANGAROO", "TORTOISE",
    "PYTHON", "JAVASCRIPT", "DATABASE", "PROGRAMMING", "ALGORITHM",
    "AUSTRIA", "GERMANY", "CANADA", "BRAZIL", "JAPAN",
    "NORWAY", "ICELAND", "ARGENTINA", "AUSTRALIA", "SWITZERLAND",
]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _hangman_display(word: str, guessed: set[str] | None = None) -> str:
    """Show one visible underscore per unguessed letter, protected from Discord Markdown."""
    guessed = guessed or set()
    display = " ".join(
        letter if letter == " " or letter.lower() in guessed else "_"
        for letter in word
    )
    return f"`{display}`"


def setup_minigames(bot: commands.Bot, db: sqlite3.Connection, register_commands: bool = True) -> None:
    register_command = bot.tree.command if register_commands else (lambda **kwargs: (lambda function: function))
    """Register commands and the message listener on the existing bot."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS minigame_scores (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            game TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            best INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, game)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS minigame_combined_leaderboards (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS minigame_leaderboard_messages (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            game TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, channel_id, game)
        )"""
    )
    db.commit()

    # Persist active rounds so inactivity and bot restarts do not discard community games.
    db.execute("""CREATE TABLE IF NOT EXISTS minigame_active_rounds (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, state_json TEXT NOT NULL, PRIMARY KEY (guild_id, channel_id))""")
    db.commit()
    active = {}
    for saved_guild_id, saved_channel_id, state_json in db.execute("SELECT guild_id, channel_id, state_json FROM minigame_active_rounds").fetchall():
        try:
            saved_state = json.loads(state_json)
            if saved_state.get("game") == "hangman":
                saved_state["guessed"] = set(saved_state.get("guessed", []))
            if saved_state.get("game") in GAMES:
                active[(saved_guild_id, saved_channel_id)] = saved_state
        except (TypeError, ValueError):
            db.execute("DELETE FROM minigame_active_rounds WHERE guild_id=? AND channel_id=?", (saved_guild_id, saved_channel_id))
    db.commit()

    def save_round(key):
        state = active.get(key)
        if state is None:
            db.execute("DELETE FROM minigame_active_rounds WHERE guild_id=? AND channel_id=?", key)
        else:
            serializable = dict(state)
            if isinstance(serializable.get("guessed"), set):
                serializable["guessed"] = sorted(serializable["guessed"])
            db.execute("INSERT INTO minigame_active_rounds(guild_id,channel_id,state_json) VALUES(?,?,?) ON CONFLICT(guild_id,channel_id) DO UPDATE SET state_json=excluded.state_json", (key[0], key[1], json.dumps(serializable, ensure_ascii=False)))
        db.commit()

    class DinoLanguageSelect(discord.ui.Select):
        def __init__(self, question_en: str, question_de: str):
            self.question_en = question_en
            self.question_de = question_de
            super().__init__(
                placeholder="Choose your language / Sprache auswählen",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(label="English", value="en", emoji="🇬🇧"),
                    discord.SelectOption(label="Deutsch", value="de", emoji="🇩🇪"),
                    discord.SelectOption(label="Français", value="fr", emoji="🇫🇷"),
                    discord.SelectOption(label="Español", value="es", emoji="🇪🇸"),
                    discord.SelectOption(label="Italiano", value="it", emoji="🇮🇹"),
                    discord.SelectOption(label="Português", value="pt", emoji="🇵🇹"),
                    discord.SelectOption(label="Nederlands", value="nl", emoji="🇳🇱"),
                    discord.SelectOption(label="Polski", value="pl", emoji="🇵🇱"),
                    discord.SelectOption(label="Русский", value="ru", emoji="🇷🇺"),
                ],
            )

        async def callback(self, interaction: discord.Interaction):
            language = self.values[0]
            question = _dino_question(self.question_en, self.question_de, language)
            if language not in ("en", "de") and question == self.question_en:
                question = await _translate_text(self.question_en, language)
            language_names = {
                "en": "English", "de": "Deutsch", "fr": "Français",
                "es": "Español", "it": "Italiano", "pt": "Português",
                "nl": "Nederlands", "pl": "Polski", "ru": "Русский",
            }
            # Only send the question privately. Never reveal the answer via this menu.
            await interaction.response.send_message(
                f"🦖 **Dino question · {language_names.get(language, 'English')}**\\n{question}",
                ephemeral=True,
            )

    class DinoLanguageView(discord.ui.View):
        def __init__(self, question_en: str, question_de: str, answer: str = ""):
            super().__init__(timeout=1800)
            self.add_item(DinoLanguageSelect(question_en, question_de))

    async def send_my_rank(interaction: discord.Interaction, game: str):
        if interaction.guild_id is None:
            await interaction.response.send_message("This button can only be used in a server.", ephemeral=True)
            return
        rows = db.execute(
            "SELECT user_id, points, wins FROM minigame_scores WHERE guild_id=? AND game=? "
            "ORDER BY points DESC, wins DESC, user_id ASC",
            (interaction.guild_id, game),
        ).fetchall()
        rank = next((index for index, row in enumerate(rows, 1) if row[0] == interaction.user.id), None)
        if rank is None:
            text = f"You don't have a score in **{GAMES[game]}** yet."
        else:
            points = rows[rank - 1][1]
            text = f"Your **{GAMES[game]}** rank is **#{rank}** out of **{len(rows)}** players, with **{points}** points."
        await interaction.response.send_message(text, ephemeral=True)

    class MyRankView(discord.ui.View):
        def __init__(self, game: str):
            super().__init__(timeout=None)
            self.game = game
            button = discord.ui.Button(
                label="See My Rank",
                style=discord.ButtonStyle.primary,
                custom_id=f"minigame:my-rank:{game}",
                emoji="🏅",
            )
            button.callback = self.show_rank
            self.add_item(button)

        async def show_rank(self, interaction: discord.Interaction):
            await send_my_rank(interaction, self.game)

    for _game in ("flag", "dinoquiz", "quiz", "hangman", "scramble"):
        bot.add_view(MyRankView(_game))

    async def update_channel_leaderboard(guild: discord.Guild, channel, game: str, move_to_latest: bool = False):
        # Counting intentionally has no leaderboard.
        if game == "count":
            return
        rows = db.execute(
            "SELECT user_id, points, wins FROM minigame_scores WHERE guild_id=? AND game=? "
            "ORDER BY points DESC, wins DESC, user_id ASC LIMIT 5",
            (guild.id, game),
        ).fetchall()
        lines = [
            f"**{index}.** <@{user_id}> — **{points}** points · {wins} wins"
            for index, (user_id, points, wins) in enumerate(rows, 1)
        ]
        embed = discord.Embed(
            title=f"🏆 {GAMES[game]} Leaderboard",
            description="\\n".join(lines) if lines else "No scores yet. Be the first to play!",
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Only the top 5 are shown. Use the button below to see your rank.")
        view = MyRankView(game)
        row = db.execute(
            "SELECT message_id FROM minigame_leaderboard_messages WHERE guild_id=? AND channel_id=? AND game=?",
            (guild.id, channel.id, game),
        ).fetchone()
        message = None
        if row:
            try:
                message = await channel.fetch_message(row[0])
                if move_to_latest and message is not None:
                    try:
                        await message.delete()
                        message = None
                    except (discord.Forbidden, discord.HTTPException):
                        # If the bot cannot move the pinned post, keep updating the existing one.
                        pass
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is None:
            message = await channel.send(embed=embed, view=view)
            db.execute(
                """INSERT INTO minigame_leaderboard_messages(guild_id,channel_id,game,message_id)
                   VALUES(?,?,?,?) ON CONFLICT(guild_id,channel_id,game)
                   DO UPDATE SET message_id=excluded.message_id""",
                (guild.id, channel.id, game, message.id),
            )
            db.commit()
            try:
                await message.pin(reason="Keep the mini-game leaderboard at the top of its game channel.")
            except (discord.Forbidden, discord.HTTPException):
                # Keep the leaderboard usable even without permission to pin.
                pass
        else:
            await message.edit(embed=embed, view=view)
        await update_combined_leaderboard(guild)

    COMBINED_LEADERBOARD_CHANNEL_ID = 1569591058734653511
    LEADERBOARD_GAMES = ("flag", "dinoquiz", "quiz", "hangman", "scramble")

    async def update_combined_leaderboard(guild: discord.Guild):
        """Keep one four-game top-five board in the configured Discord channel."""
        channel = bot.get_channel(COMBINED_LEADERBOARD_CHANNEL_ID)
        if channel is None:
            try:
                channel = await bot.fetch_channel(COMBINED_LEADERBOARD_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
            return

        embed = discord.Embed(
            title="🏆 MINI-GAME HALL OF FAME",
            description=(
                "**TOP 5 PLAYERS · EACH GAME**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Counting is not included."
            ),
            color=discord.Color.from_rgb(244, 190, 70),
        )
        game_icons = {
            "flag": "🌍",
            "dinoquiz": "🦖",
            "quiz": "🧠",
            "hangman": "🔤",
            "scramble": "🔀",
        }
        for game_key in LEADERBOARD_GAMES:
            rows = db.execute(
                "SELECT user_id, points, wins FROM minigame_scores "
                "WHERE guild_id=? AND game=? ORDER BY points DESC, wins DESC, user_id ASC LIMIT 5",
                (guild.id, game_key),
            ).fetchall()
            if rows:
                entries = []
                for place, (user_id, points, wins) in enumerate(rows, 1):
                    entries.append(f"**<@{user_id}>** — **{points}** Punkte")
                value = "\n".join(entries)
            else:
                value = "_No players yet — be the first to claim a spot!_"
            embed.add_field(
                name=f"{game_icons.get(game_key, '🎮')}  {GAMES[game_key].upper()}",
                value=value,
                inline=False,
            )
        embed.set_footer(text="🔄 Live leaderboard • Automatically updated after every score")

        saved = db.execute(
            "SELECT message_id FROM minigame_combined_leaderboards WHERE channel_id=? AND guild_id=?",
            (COMBINED_LEADERBOARD_CHANNEL_ID, guild.id),
        ).fetchone()
        message = None
        if saved:
            try:
                message = await channel.fetch_message(saved[0])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is None:
            message = await channel.send(embed=embed)
            db.execute(
                """INSERT INTO minigame_combined_leaderboards(channel_id,guild_id,message_id)
                   VALUES(?,?,?) ON CONFLICT(channel_id) DO UPDATE SET
                   guild_id=excluded.guild_id,message_id=excluded.message_id""",
                (COMBINED_LEADERBOARD_CHANNEL_ID, guild.id, message.id),
            )
            db.commit()
            try:
                await message.pin(reason="Keep the combined mini-game top-five leaderboards visible.")
            except (discord.Forbidden, discord.HTTPException):
                pass
        else:
            await message.edit(embed=embed)

    def key_for(interaction: discord.Interaction):
        return (interaction.guild_id, interaction.channel_id)

    def award(guild_id: int, user_id: int, game: str, points: int = 1, win: bool = False, best: int | None = None):
        row = db.execute(
            "SELECT points, wins, best FROM minigame_scores WHERE guild_id=? AND user_id=? AND game=?",
            (guild_id, user_id, game),
        ).fetchone()
        old_points, old_wins, old_best = row or (0, 0, 0)
        new_best = max(old_best, best if best is not None else points)
        db.execute(
            """INSERT INTO minigame_scores(guild_id,user_id,game,points,wins,best)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(guild_id,user_id,game) DO UPDATE SET
                 points=excluded.points, wins=excluded.wins, best=excluded.best""",
            (guild_id, user_id, game, old_points + points, old_wins + int(win), new_best),
        )
        db.commit()

    async def start_round(interaction: discord.Interaction, game: str, state: dict, intro: str):
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("These games can only be played in a server channel.", ephemeral=True)
            return
        key = key_for(interaction)
        if key in active:
            if active[key]["game"] == game:
                del active[key]
                save_round(key)
                await interaction.response.send_message(f"🛑 {GAMES[game]} has been stopped.")
            else:
                await interaction.response.send_message(
                    "Another game is already running in this channel. Stop it before starting a different one.",
                    ephemeral=True,
                )
            return
        # Acknowledge the slash command before leaderboard I/O, which can take long enough
        # for Discord's initial interaction-response deadline to expire.
        await interaction.response.defer()
        active[key] = {"game": game, **state}
        save_round(key)
        # Repost the leaderboard first so it appears immediately above the new round prompt.
        if game != "count":
            try:
                await update_channel_leaderboard(
                    interaction.guild, interaction.channel, game, move_to_latest=True
                )
            except discord.HTTPException:
                pass
        await interaction.edit_original_response(content=intro)

    @register_command(name="count", description="Start or stop the counting game in this channel")
    async def count_command(interaction: discord.Interaction):
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("This game can only be played in a server channel.", ephemeral=True)
            return
        key = key_for(interaction)
        current = active.get(key)
        if current and current["game"] == "count":
            del active[key]
            save_round(key)
            await interaction.response.send_message("🛑 Counting has been stopped.")
            return
        if current:
            await interaction.response.send_message("Another game is already running in this channel.", ephemeral=True)
            return
        active[key] = {"game": "count", "expected": 1, "last_user_id": None}
        save_round(key)
        await interaction.response.send_message(
            "🔢 **Counting has started!** Send **1** to begin, then count up by one in separate messages. "
            "Correct numbers get ✅. A wrong number resets the count to **1**. Use /count again to stop."
        )

    @bot.tree.command(name="flag", description="Start a flag guessing round")
    async def flag_command(interaction: discord.Interaction):
        flag, answer, aliases = random.choice(FLAGS)
        await start_round(
            interaction, "flag", {"answer": answer, "aliases": aliases},
            f"🌍 **Flag Guessing!**\nWhich country does this flag belong to?\n\n# {flag}\n\nType your answer in chat!"
        )

    @bot.tree.command(name="dinoquiz", description="Start an Evrima dinosaur guessing round")
    async def dinoquiz_command(interaction: discord.Interaction):
        question_en, answer, aliases, question_de, answer_de = random.choice(DINO_QUESTIONS)
        await start_round(
            interaction, "dinoquiz",
            {"answer": answer, "aliases": aliases, "question_en": question_en,
             "question_de": question_de, "answer_de": answer_de, "hint_index": 0},
            f"🦖 **Dino Guessing!**\n{question_en}\n\nType your answer in chat!\n"
            "Use the menu below to read the question privately in your language. The answer stays secret!",
        )
        if interaction.guild and interaction.channel:
            key = (interaction.guild_id, interaction.channel_id)
            try:
                msg = await interaction.original_response()
                await msg.edit(view=DinoLanguageView(question_en, question_de, answer))
            except discord.HTTPException:
                pass

    class QuizLanguageSelect(discord.ui.Select):
        def __init__(self, question: str, options: list[str]):
            self.question = question
            self.options_en = options
            super().__init__(
                placeholder="Choose your language / Sprache auswählen",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(label="English", value="en", emoji="🇬🇧"),
                    discord.SelectOption(label="Deutsch", value="de", emoji="🇩🇪"),
                    discord.SelectOption(label="Français", value="fr", emoji="🇫🇷"),
                    discord.SelectOption(label="Español", value="es", emoji="🇪🇸"),
                    discord.SelectOption(label="Italiano", value="it", emoji="🇮🇹"),
                    discord.SelectOption(label="Português", value="pt", emoji="🇵🇹"),
                    discord.SelectOption(label="Nederlands", value="nl", emoji="🇳🇱"),
                    discord.SelectOption(label="Polski", value="pl", emoji="🇵🇱"),
                    discord.SelectOption(label="Русский", value="ru", emoji="🇷🇺"),
                ],
            )

        async def callback(self, interaction: discord.Interaction):
            language = self.values[0]
            question = self.question
            options = self.options_en
            if language != "en":
                q_index = next((i for i, item in enumerate(QUIZ_QUESTIONS) if item[0] == self.question), None)
                translated = QUIZ_TRANSLATIONS.get(language, [])
                if q_index is not None and q_index < len(translated):
                    question, options = translated[q_index]
                else:
                    translated_texts = await _translate_texts([self.question, *self.options_en], language)
                    question, options = translated_texts[0], translated_texts[1:]
            names = {
                "en": "English", "de": "Deutsch", "fr": "Français",
                "es": "Español", "it": "Italiano", "pt": "Português",
                "nl": "Nederlands", "pl": "Polski", "ru": "Русский",
            }
            labels = ["A", "B", "C", "D"]
            choices = "\n".join(f"**{labels[i]}.** {option}" for i, option in enumerate(options))
            # Keep the correct choice unmarked; the answer is never disclosed in this private language menu.
            await interaction.response.send_message(
                f"🧠 **Quiz · {names.get(language, 'English')}**\n{question}\n\n{choices}\n\nType your answer or A–D in the public game chat.",
                ephemeral=True,
            )

    class QuizLanguageView(discord.ui.View):
        def __init__(self, question: str, options: list[str]):
            super().__init__(timeout=1800)
            self.add_item(QuizLanguageSelect(question, options))

    @bot.tree.command(name="quiz", description="Start a general gaming and knowledge quiz")
    async def quiz_command(interaction: discord.Interaction):
        question, options, correct_index = random.choice(QUIZ_QUESTIONS)
        labels = ["A", "B", "C", "D"]
        prompt = "\n".join(f"**{labels[i]}.** {option}" for i, option in enumerate(options))
        aliases = [_normalize(options[correct_index]), labels[correct_index].lower()]
        await start_round(
            interaction, "quiz",
            {"answer": options[correct_index], "aliases": aliases, "asked_questions": [question]},
            f"🧠 **Quiz Time!**\n{question}\n\n{prompt}\n\nType the answer or its letter (A–D). "
            "Use the dropdown to read the question and choices privately in your language; the answer stays secret!",
        )
        if interaction.guild and interaction.channel:
            try:
                msg = await interaction.original_response()
                await msg.edit(view=QuizLanguageView(question, options))
            except discord.HTTPException:
                pass

    @bot.tree.command(name="wordscramble", description="Start or stop an English word scramble game")
    async def wordscramble_command(interaction: discord.Interaction):
        word = random.choice(SCRAMBLE_WORDS)
        scrambled = _scramble_word(word)
        await start_round(
            interaction, "scramble", {"word": word, "scrambled": scrambled},
            f"🔀 **Word Scramble!**\nUnscramble the letters to find the English word:\n\n**{scrambled}**\n\nType your answer in chat! The first correct answer earns **2 points**. Use /wordscramble again to stop."
        )

    @bot.tree.command(name="hangman", description="Start an English hangman word game")
    async def hangman_command(interaction: discord.Interaction):
        word = random.choice(HANGMAN_WORDS)
        state = {"word": word, "guessed": set(), "wrong": 0, "max_wrong": 6}
        display = _hangman_display(word)
        await start_round(
            interaction, "hangman", state,
            f"🔤 **Hangman!**\nGuess the hidden word by sending one letter or the full word. "
            f"You have **6** wrong guesses.\n\n{display}"
        )

    game_choices = [
        app_commands.Choice(name="Counting", value="count"),
        app_commands.Choice(name="Flag Guessing", value="flag"),
        app_commands.Choice(name="Dino Guessing", value="dinoquiz"),
        app_commands.Choice(name="Quiz", value="quiz"),
        app_commands.Choice(name="Hangman", value="hangman"),
        app_commands.Choice(name="Word Scramble", value="scramble"),
    ]

    @bot.tree.command(name="resetminigames", description="Reset all mini-game scores and leaderboards")
    @app_commands.default_permissions(administrator=True)
    async def resetminigames_command(interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only server administrators can reset mini-game scores.", ephemeral=True
            )
            return

        # Remove the saved leaderboard posts for this server; scores in other servers are untouched.
        board_rows = db.execute(
            "SELECT channel_id, message_id FROM minigame_leaderboard_messages WHERE guild_id=?",
            (interaction.guild.id,),
        ).fetchall()
        deleted_boards = 0
        for channel_id, message_id in board_rows:
            channel = interaction.guild.get_channel(channel_id)
            if channel is None:
                continue
            try:
                board_message = await channel.fetch_message(message_id)
                await board_message.delete()
                deleted_boards += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        db.execute("DELETE FROM minigame_scores WHERE guild_id=?", (interaction.guild.id,))
        db.execute(
            "DELETE FROM minigame_leaderboard_messages WHERE guild_id=?",
            (interaction.guild.id,),
        )
        db.commit()

        for key in list(active):
            if key[0] == interaction.guild.id:
                del active[key]

        await interaction.response.send_message(
            "✅ All mini-game points, wins, personal stats, active rounds, and saved leaderboard posts "
            f"have been reset for this server. Deleted {deleted_boards} leaderboard post(s). "
            "The bot's code and other features are unchanged. New scores start from zero."
        )

    @bot.tree.command(name="leaderboard", description="Show the leaderboard for a mini-game")
    @app_commands.describe(game="Choose which game's leaderboard to view")
    @app_commands.choices(game=game_choices)
    async def leaderboard_command(interaction: discord.Interaction, game: app_commands.Choice[str]):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        rows = db.execute(
            "SELECT user_id, points, wins, best FROM minigame_scores WHERE guild_id=? AND game=? "
            "ORDER BY points DESC, wins DESC, best DESC LIMIT 10",
            (interaction.guild_id, game.value),
        ).fetchall()
        if not rows:
            await interaction.response.send_message(f"No scores yet for **{GAMES[game.value]}**. Start a game with /{game.value}!")
            return
        lines = []
        for index, (user_id, points, wins, best) in enumerate(rows, 1):
            lines.append(f"**{index}.** <@{user_id}> — **{points}** points · {wins} wins · best: {best}")
        embed = discord.Embed(
            title=f"🏆 {GAMES[game.value]} Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="mystats", description="Show your mini-game statistics")
    async def mystats_command(interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        rows = db.execute(
            "SELECT game, points, wins, best FROM minigame_scores WHERE guild_id=? AND user_id=? ORDER BY game",
            (interaction.guild_id, interaction.user.id),
        ).fetchall()
        if not rows:
            await interaction.response.send_message("You don't have mini-game stats yet. Start playing to earn points!")
            return
        lines = [
            f"**{GAMES.get(game, game)}** — {points} points · {wins} wins · best: {best}"
            for game, points, wins, best in rows
        ]
        embed = discord.Embed(
            title=f"📊 {interaction.user.display_name}'s Mini-Game Stats",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    @bot.listen("on_message")
    async def handle_minigame_message(message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        key = (message.guild.id, message.channel.id)
        state = active.get(key)
        if state is None:
            return
        game = state["game"]
        content = message.content.strip()
        if not content:
            return

        if game == "count":
            if not re.fullmatch(r"\d+", content):
                return
            number = int(content)
            expected = state["expected"]
            last_user_id = state.get("last_user_id")
            if number == expected and message.author.id != last_user_id:
                try:
                    await message.add_reaction("✅")
                except discord.HTTPException:
                    pass
                award(message.guild.id, message.author.id, "count", points=1, best=number)
                state["expected"] += 1
                state["last_user_id"] = message.author.id
                save_round(key)
            else:
                if number == expected:
                    reason = "Two numbers in a row must not be sent by the same person."
                else:
                    reason = f"The next number should have been **{expected}**."
                state["expected"] = 1
                state["last_user_id"] = None
                save_round(key)
                await message.channel.send(
                    f"❌ **Counting error!** {message.author.mention}, {reason} "
                    f"The count has been reset to **1**. Let's start again!"
                )
            return

        normalized = _normalize(content)
        if game in ("flag", "dinoquiz", "quiz"):
            accepted = {_normalize(value) for value in state["aliases"]}
            if normalized not in accepted:
                if game == "quiz":
                    try:
                        await message.add_reaction("❌")
                    except discord.HTTPException:
                        pass
                    await message.channel.send(
                        f"❌ {message.author.mention}, that's not the right answer. Try again!",
                        delete_after=10,
                    )
                elif game == "dinoquiz":
                    hints = DINO_HINTS.get(state["answer"], [])
                    hint_index = state.get("hint_index", 0)
                    if hint_index < min(10, len(hints)):
                        hint = hints[hint_index]
                        state["hint_index"] = hint_index + 1
                        await message.channel.send(
                            f"💡 **Hint {hint_index + 1}/10** for {message.author.mention}: {hint}"
                        )
                    else:
                        await message.channel.send(
                            f"💡 {message.author.mention}, you've used all 10 hints for this question. Keep guessing!"
                        )
                return
            award(message.guild.id, message.author.id, game, points=1, win=True, best=1)
            try:
                await update_channel_leaderboard(message.guild, message.channel, game)
            except discord.HTTPException:
                pass
            answer = state["answer"]
            if game == "flag":
                flag, next_answer, next_aliases = random.choice(FLAGS)
                state.update(answer=next_answer, aliases=next_aliases)
                save_round(key)
                next_prompt = f"🌍 **Next Flag!**\nWhich country does this flag belong to?\n\n# {flag}\n\nType your answer in chat!"
            elif game == "dinoquiz":
                question_en, next_answer, next_aliases, question_de, answer_de = random.choice(DINO_QUESTIONS)
                state.update(answer=next_answer, aliases=next_aliases, question_en=question_en,
                             question_de=question_de, answer_de=answer_de, hint_index=0)
                save_round(key)
                next_prompt = f"🦖 **Next Dino!**\n{question_en}\n\nType your answer in chat!"
            else:
                asked_questions = state.setdefault("asked_questions", [])
                question, options, correct_index = _next_quiz_question(asked_questions)
                asked_questions.append(question)
                labels = ["A", "B", "C", "D"]
                prompt = "\n".join(f"**{labels[i]}.** {option}" for i, option in enumerate(options))
                next_aliases = [_normalize(options[correct_index]), labels[correct_index].lower()]
                state.update(answer=options[correct_index], aliases=next_aliases)
                save_round(key)
                next_prompt = f"🧠 **Next Question!**\n{question}\n\n{prompt}\n\nType the answer or its letter (A–D)."
            if game == "dinoquiz":
                await message.channel.send(
                    f"✅ {message.author.mention} got it right! The answer was **{answer}**. +1 point!\n\n{next_prompt}",
                    view=DinoLanguageView(state["question_en"], state["question_de"], state["answer"]),
                )
            elif game == "quiz":
                await message.channel.send(
                    f"✅ {message.author.mention} got it right! The answer was **{answer}**. +1 point!\n\n{next_prompt}",
                    view=QuizLanguageView(question, options),
                )
            else:
                await message.channel.send(
                    f"✅ {message.author.mention} got it right! The answer was **{answer}**. +1 point!\n\n{next_prompt}"
                )
            return

        if game == "scramble":
            answer = _normalize(state["word"])
            if normalized != answer:
                return
            award(message.guild.id, message.author.id, "scramble", points=2, win=True, best=2)
            try:
                await update_channel_leaderboard(message.guild, message.channel, "scramble")
            except discord.HTTPException:
                pass
            solved_word = state["word"]
            next_word = random.choice([item for item in SCRAMBLE_WORDS if item != solved_word] or SCRAMBLE_WORDS)
            next_scrambled = _scramble_word(next_word)
            state.update(word=next_word, scrambled=next_scrambled)
            save_round(key)
            await message.channel.send(
                f"🎉 {message.author.mention} solved it! The word was **{solved_word}**. +2 points!\n\n"
                f"🔀 **Next word!**\nUnscramble: **{next_scrambled}**\nType your answer in chat!"
            )
            return

        if game == "hangman":
            word = state["word"]
            guess = _normalize(content).replace(" ", "")
            if len(guess) == 1 and guess.isalpha():
                if guess in state["guessed"]:
                    await message.channel.send(f"{message.author.mention}, that letter has already been guessed.")
                    return
                state["guessed"].add(guess)
                if guess not in word.lower():
                    state["wrong"] += 1
                save_round(key)
            elif guess == _normalize(word).replace(" ", ""):
                award(message.guild.id, message.author.id, "hangman", points=2, win=True, best=2)
                try:
                    await update_channel_leaderboard(message.guild, message.channel, "hangman")
                except discord.HTTPException:
                    pass
                next_word = random.choice([item for item in HANGMAN_WORDS if item != word] or HANGMAN_WORDS)
                state.update(word=next_word, guessed=set(), wrong=0)
                save_round(key)
                display = _hangman_display(next_word)
                await message.channel.send(
                    f"🎉 {message.author.mention} solved it! The word was **{word}**. +2 points!\n\n"
                    f"🔤 **Next word!** Guess one letter or the complete word. You have **6** wrong guesses.\n\n{display}"
                )
                return
            else:
                await message.channel.send("Please guess one letter or the complete word.")
                return

            display = _hangman_display(word, state["guessed"])
            if "_" not in display:
                award(message.guild.id, message.author.id, "hangman", points=2, win=True, best=2)
                try:
                    await update_channel_leaderboard(message.guild, message.channel, "hangman")
                except discord.HTTPException:
                    pass
                next_word = random.choice([item for item in HANGMAN_WORDS if item != word] or HANGMAN_WORDS)
                state.update(word=next_word, guessed=set(), wrong=0)
                save_round(key)
                next_display = _hangman_display(next_word)
                await message.channel.send(
                    f"🎉 {message.author.mention} solved it! The word was **{word}**. +2 points!\n\n"
                    f"🔤 **Next word!** Guess one letter or the complete word. You have **6** wrong guesses.\n\n{next_display}"
                )
            elif state["wrong"] >= state["max_wrong"]:
                next_word = random.choice([item for item in HANGMAN_WORDS if item != word] or HANGMAN_WORDS)
                state.update(word=next_word, guessed=set(), wrong=0)
                save_round(key)
                next_display = _hangman_display(next_word)
                await message.channel.send(
                    f"💀 No guesses left! The word was **{word}**.\n\n"
                    f"🔤 **Next word!** Keep playing — guess one letter or the complete word. "
                    f"You have **6** wrong guesses.\n\n{next_display}"
                )
            else:
                await message.channel.send(
                    f"{display}\nWrong guesses: **{state['wrong']}/{state['max_wrong']}** · "
                    f"Guessed: {', '.join(sorted(state['guessed'])) or 'none'}"
                )

    refreshed_after_ready = False

    @bot.listen("on_ready")
    async def refresh_active_minigame_rounds():
        nonlocal refreshed_after_ready
        if refreshed_after_ready:
            return
        refreshed_after_ready = True
        # Active rounds are persisted in SQLite and their original prompts already
        # exist in Discord. Never create replacement "Fresh Round" messages on
        # startup/reconnect: repeated Railway restarts would otherwise spam the
        # channel and reset the question/answer while players are still guessing.
        for guild in bot.guilds:
            try:
                await update_combined_leaderboard(guild)
            except discord.HTTPException:
                pass

    async def start_dashboard_minigame(guild, channel, game: str):
        """Start or stop a mini-game from the authenticated web dashboard."""
        if game not in GAMES:
            raise ValueError("Unknown mini-game.")
        if guild is None or channel is None or channel.guild.id != guild.id:
            raise ValueError("Please select a valid text channel.")
        key = (guild.id, channel.id)
        current = active.get(key)
        if current:
            if current.get("game") == game:
                del active[key]
                save_round(key)
                await channel.send(f"🛑 **{GAMES[game]}** has been stopped from the dashboard.")
                return f"{GAMES[game]} stopped in #{channel.name}."
            raise ValueError(f"{GAMES[current['game']]} is already running in this channel. Stop it first.")

        view = None
        if game == "count":
            state = {"game": "count", "expected": 1, "last_user_id": None}
            intro = "🔢 **Counting has started!** Send **1** to begin, then count up by one in separate messages. Correct numbers get ✅. A wrong number resets the count to **1**."
        elif game == "flag":
            flag, answer, aliases = random.choice(FLAGS)
            state = {"game": game, "answer": answer, "aliases": aliases}
            intro = f"🌍 **Flag Guessing!**\nWhich country does this flag belong to?\n\n# {flag}\n\nType your answer in chat!"
        elif game == "dinoquiz":
            q_en, answer, aliases, q_de, answer_de = random.choice(DINO_QUESTIONS)
            state = {"game": game, "answer": answer, "aliases": aliases, "question_en": q_en, "question_de": q_de, "answer_de": answer_de, "hint_index": 0}
            intro = f"🦖 **Dino Quiz!**\n{q_en}\n\nType your answer in chat! Use the language menu to read the question privately."
            view = DinoLanguageView(q_en, q_de, answer)
        elif game == "quiz":
            question, options, correct_index = random.choice(QUIZ_QUESTIONS)
            labels = ["A", "B", "C", "D"]
            prompt = "\n".join(f"**{labels[i]}.** {option}" for i, option in enumerate(options))
            aliases = [_normalize(options[correct_index]), labels[correct_index].lower()]
            state = {"game": game, "answer": options[correct_index], "aliases": aliases, "asked_questions": [question]}
            intro = f"🧠 **Quiz Time!**\n{question}\n\n{prompt}\n\nType the answer or its letter (A–D). Use the language menu to read it privately."
            view = QuizLanguageView(question, options)
        elif game == "scramble":
            word = random.choice(SCRAMBLE_WORDS)
            scrambled = _scramble_word(word)
            state = {"game": game, "word": word, "scrambled": scrambled}
            intro = f"🔀 **Word Scramble!**\nUnscramble the English word:\n\n**{scrambled}**\n\nThe first correct answer earns **2 points**."
        else:
            word = random.choice(HANGMAN_WORDS)
            state = {"game": game, "word": word, "guessed": set(), "wrong": 0, "max_wrong": 6}
            intro = f"🔤 **Hangman!**\nGuess the hidden word by sending one letter or the full word. You have **6** wrong guesses.\n\n{_hangman_display(word)}"

        active[key] = state
        save_round(key)
        if game != "count":
            try:
                await update_channel_leaderboard(guild, channel, game, move_to_latest=True)
            except discord.HTTPException:
                pass
        await channel.send(intro, view=view)
        return f"{GAMES[game]} started in #{channel.name}."

    bot.start_dashboard_minigame = start_dashboard_minigame

    # Expose state for optional diagnostics and future graceful shutdown support.
    bot._minigame_active_rounds = active
