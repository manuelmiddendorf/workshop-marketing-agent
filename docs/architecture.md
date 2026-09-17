# Architektur: workshop-marketing-agent

Stand: 17. September 2026. Status: **Entwurf zur gemeinsamen Prüfung**.

Die bestätigten Produktentscheidungen stehen in [v1-scope.md](v1-scope.md).
Die technische Ausgestaltung in diesem Dokument ist ein Vorschlag für die spätere
Implementierung. Offene Punkte sind ausdrücklich markiert. Es gibt noch keinen
Produktionscode und keine geprüfte Integration mit der bestehenden Lehrer-App.

## 1. Ziel und Architekturprinzipien

Das System hilft einem Yogastudio, bestehende Workshops mit wenig Arbeitsaufwand
über passende Kanäle zu bewerben. Zugleich soll es als öffentliches Portfolio
nachvollziehbares Python-, Applied-LLM- und Evaluation-Engineering zeigen.

- Eigenständiges, installierbares Python-Package mit expliziter Pipeline.
- Gemeinsames Workshop-Datenmodell statt Abhängigkeit vom Firestore-Schema.
- Deterministische Regeln für Fakten, Kanäle, Links, Status und Aktionen.
- LLM für sprachliche Entwürfe und Überarbeitung; Menschen geben Fassungen frei.
- Bestehende Lehrer-App für Bedienung; Firebase-Anbindung für Betrieb und Daten.
- Ergebnisse und Fehler pro Kanal; erfolgreiche Teilergebnisse bleiben erhalten.
- Kleine verständliche Änderungen und Evaluation von Beginn an.

Ausgangsgröße: 1–2 Workshops pro Monat, 2–3 Lehrer, Werbebeginn 2–3 Monate vorher.
Das Design optimiert einen überschaubaren Betrieb und verständlichen Code.

## 2. Verantwortlichkeiten

| Bestandteil | Verantwortlich für |
| --- | --- |
| Lehrer-App | Entwürfe anzeigen, direkt bearbeiten, Änderungswünsche eingeben, Alternativen und Bilder wählen, Kanäle deaktivieren, freigeben, manuelle Veröffentlichungen bestätigen |
| Firebase-Anbindung | Authentifizierung und Workshop-Berechtigungen, Datenmapping, Bildreferenzen, serverseitige Secrets, dauerhafte Speicherung, atomare Reservierung von Aktionen, Verknüpfung mit Buchungen |
| Python-Package | Normalisierung des öffentlichen Datenmodells, Kanaleignung, Promptaufbau, Generierung und Überarbeitung, Validierung, fachliche Zustandsregeln, Linkparameter, Kanaladapter, Evaluation |
| Kanaladapter | Kanalspezifische Felder und Grenzen, Darstellung, Vorbereitung des Eintrags und optional offizielle Veröffentlichungs- und Aktualisierungsaufrufe |

Die Firebase-Anbindung führt das Package serverseitig in Python aus. Aufrufe aus
der Web-App sind über [Firebase Callable Functions](https://firebase.google.com/docs/functions/callable)
möglich. Das Package importiert keine anwendungsspezifischen Firestore-Modelle.
Für Evaluation und Tests kann es auch ohne Firebase ausgeführt werden.

Die Integrationsschicht speichert Zustände; das Package definiert ihre Bedeutung
und erlaubte Übergänge. Konkrete Speicherung und API-Zugänge bleiben außerhalb
der fachlichen Logik. Der genaue Ausführungsmodus ist unter Abschnitt 12 offen.

## 3. Daten und Faktentreue

Die Anbindung liefert ein geprüftes Abbild der Workshopdaten mit stabiler
Workshop-ID und Quellversion. Es umfasst nach Bedarf Titel, Originalbeschreibung,
Zeitzone, Beginn und Ende, Ort, Zielgruppe, Altersgrenzen, Leistungen,
Buchungsadresse, Preise mit Währung, Frühbucherfristen und Bildreferenzen.
Fehlende optionale Angaben bleiben unbekannt. Pflichtangaben hängen vom Kanal ab.

Frühbucherdaten liegen bereits strukturiert in Firestore. Sie werden übernommen,
nicht aus dem Beschreibungstext erschlossen. Fristen müssen eindeutig als
Zeitpunkt mit Zeitzone interpretierbar sein. Die Bedeutung eines nur als Datum
gespeicherten Fristendes muss beim Mapping geklärt werden.

Der ausführliche Originaltext bleibt unverändert, sofern ein Lehrer seine
Überarbeitung nicht ausdrücklich anstößt. Sichtbare Seitenelemente wie
„Vergangen“ und HTML-Kodierungen werden bei der Eingangsaufbereitung bereinigt.
Ein Widerspruch zwischen Beschreibung und strukturierten Fakten muss vor einer
betroffenen Veröffentlichung geklärt werden. Er wird nicht still aufgelöst.

Datum, Uhrzeit, Preis, Altersangaben und Buchungslink werden für die Ausgabe
deterministisch formatiert. Zeitabhängige Angaben werden bei Generierung und
erneut vor Veröffentlichung geprüft. Ein Frühbucherhinweis nennt seine Frist;
abgelaufene Angebote dürfen nicht als aktuell beworben werden.

Freie Sprache kann trotzdem unbelegte Behauptungen enthalten. Schema- und
Faktenprüfungen garantieren keine vollständige semantische Richtigkeit.
Eingeschränkte Textfelder, semantische Evaluation und menschliche Prüfung
ergänzen sich. Insbesondere dürfen Eignung für Kinder, Leistungen, Qualifikationen,
Verfügbarkeit und Wirkversprechen nicht aus plausiblen Annahmen entstehen.

## 4. Pipeline und Promptaufbau

Workshop laden → normalisieren → Konflikte prüfen → Kanäle empfehlen → Texte
erzeugen → validieren → menschlich prüfen → veröffentlichen oder vorbereiten →
Status speichern → Klicks und Buchungen zuordnen.

Die Kanalauswahl arbeitet mit nachvollziehbaren Regeln und liefert eine kurze
Begründung. Beispielsweise setzt eine Empfehlung für einen Familienkanal eine
passende tatsächliche Zielgruppe voraus. Sprachliche Anpassung erweitert nicht
die Eignung des Workshops. Lehrer können geeignete Kanäle auswählen oder abwählen;
fehlende Pflichtangaben bleiben Veröffentlichungshindernisse.

Ein Prompt wird aus überschaubaren, versionierten Bestandteilen zusammengesetzt:

1. Gemeinsame Regeln und Grenzen der Textgestaltung.
2. Geprüfte Workshopfakten und separat gekennzeichnete Originalbeschreibung.
3. Zielgruppe, Kanalregeln und Stil: warm, herzlich, verständlich.
4. Zweck der Werberunde und Zeitpunkt der Generierung.
5. Bei Überarbeitung: aktueller Entwurf und Änderungswunsch.
6. Schema für die erlaubten Ausgabefelder.

Geplant sind das offizielle OpenAI-Python-SDK, Pydantic und Structured Outputs.
Die [offizielle Dokumentation](https://developers.openai.com/api/docs/guides/structured-outputs)
beschreibt Pydantic-basierte Schemas. Schemaerfolg ersetzt keine Inhaltsprüfung;
Ablehnungen, unvollständige Antworten und Validierungsfehler werden separat behandelt.

Pro Generierung werden Modellkennung, Promptversion, Schemaversion, Quellversion,
Zeitpunkt und verfügbare Nutzungsdaten festgehalten. Workshoptexte und
Änderungswünsche sind Eingabedaten und können keine Systemregeln, Berechtigungen,
Zieladressen oder Veröffentlichungsaktionen ändern.

Ein guter Entwurf wird vorausgewählt; bis zu zwei weitere Varianten können auf
Wunsch erzeugt werden. Eine Überarbeitung verwendet die aktuelle Fassung, bewahrt
die vorherige und benötigt nach Übernahme erneut Prüfung und Freigabe.

## 5. Kampagnen, Fassungen und gespeicherter Zustand

Vorgeschlagenes Modell: Ein Workshop hat eine Marketingkampagne mit mehreren
Werberunden. Eine Runde unterscheidet etwa Erstankündigung und Erinnerung.
Eine Korrektur einer Veröffentlichung ist eine Aktualisierung mit Bezug auf den
bestehenden Beitrag; sie ist nicht automatisch eine neue Werberunde.

| Information | Zweck |
| --- | --- |
| Kampagne und Runde | Workshopbezug, Zweck, Ersteller, Zeitpunkte, ausgewählte Kanäle |
| Kanalentwurf und Fassungen | Textfelder, Varianten, Änderungswunsch, Bild, fertiger Link, Quellversion, Prüfungsergebnis |
| Freigabe | Freigebende Person, Zeitpunkt, genaue Fassung einschließlich Bild und Link |
| Veröffentlichungsdatensatz | Kanal, externe Beitrags-ID und URL soweit verfügbar, veröffentlichte Fassung, Erstellungs- oder Aktualisierungszeitpunkt |
| Ausführungsversuch | Operation, stabile Wiederholungskennung, Bearbeitungszustand, Fehlerklasse, Versuche und Zeitpunkte |
| Manuelle Bestätigung | Wer wann Einreichung oder Veröffentlichung bestätigt hat, optional öffentlicher Link |
| Zuordnung | Kampagne, Runde, Kanal, ausgewählte Variante, Klickreferenz und bestätigte Buchungsreferenz |

Bearbeitungszustand und Veröffentlichungszustand werden getrennt gespeichert.
Eine mögliche kleine Zustandsmenge ist:

- Entwurf: in Erstellung, Prüfung erforderlich, freigegeben, veraltet oder Fehler.
- Veröffentlichung: nicht gestartet, in Ausführung, eingereicht, veröffentlicht,
  fehlgeschlagen oder Ausgang unbekannt.
- Zusätzlich: Kanal deaktiviert und Änderung erforderlich, falls zutreffend.

„Zum Kopieren bereit“ ist eine Anzeige aus gültigem Entwurf und manuellem Modus.
„Eingereicht“ bedeutet bei redaktionellen Plattformen noch nicht „veröffentlicht“.
Ein bestehender veröffentlichter Beitrag bleibt dokumentiert, auch wenn eine neue
Fassung noch geprüft wird oder ihre Aktualisierung fehlschlägt.

Freigaben gelten für eine konkrete Fassung. Text-, Bild- oder Linkänderungen
machen sie ungültig. Geänderte relevante Workshopfakten und abgelaufene Preisfristen
lösen eine erneute Prüfung aus. Direkt vor dem Versand vergleicht die Anbindung
den aktuellen Stand mit der freigegebenen Grundlage.

Bereits veröffentlichte Beiträge mit Änderungsbedarf werden sichtbar markiert,
sobald der aktuelle Stand geprüft wird. V1 verspricht keine zeitgesteuerte
Hintergrundkorrektur. Lehrer stoßen Aktualisierungen und Erinnerungen an.

## 6. Kanaladapter

Ein Adapter wird ausdrücklich in einer kleinen Registry registriert. Neue
Kanalregeln sollen keinen Umbau der zentralen Pipeline verlangen. Die fachliche
Schnittstelle besteht aus folgenden Verantwortlichkeiten, nicht aus einer
universellen Plugin- oder Workflow-Engine:

| Teil | Inhalt |
| --- | --- |
| Beschreibung | Kanal-ID, unterstützte Modi, Zielgruppen, Bildanforderungen, Pflichtfelder und bekannte Grenzen |
| Eignung | Deterministisches Ergebnis mit nachvollziehbarer Begründung |
| Entwurf | Ausgabeschema, Kanalinstruktionen, Darstellung und Validierung |
| Vorbereitung | Kopierbare Felder, Zielseite, Bildreferenz und nötige Handlungsschritte |
| Optionale Veröffentlichung | Offizielles Erstellen und gegebenenfalls Aktualisieren, strukturierte Ergebnisse und Fehler |

Modi: `automatic` veröffentlicht nach Freigabe über eine unterstützte Schnittstelle;
`assisted` bereitet Formulare oder Einreichungen vor; `manual` liefert Text, Link
und eine anschließende Bestätigungsmöglichkeit. Ein Adapter muss nicht alle Modi
oder eine Veröffentlichungsfunktion besitzen. Kontokonfiguration und Credentials
bestimmen zusätzlich, welche unterstützten Aktionen aktuell verfügbar sind.

Google ist der geplante erste externe automatische Kanal. Rausgegangen und HIMBEER
beginnen als unterstützte manuelle Einträge. Ein manueller Ausweichweg darf
angeboten werden, ohne einen fehlgeschlagenen API-Versuch als Erfolg auszugeben.
Die eigene Workshopseite bleibt der kanonische Informations- und Buchungsort;
ihr bestehender Veröffentlichungsablauf wird angebunden, nicht automatisch durch
einen zweiten redaktionellen Ablauf ersetzt.

## 7. Bilder

Der Pool ist ein Katalog vorhandener Workshop- und Webseitenbilder. Originale
müssen dafür nicht dupliziert werden. Er enthält stabile Bildkennungen,
Referenzen, kurze Beschreibungen, Themen-Schlagwörter sowie Freigaben und
gegebenenfalls Bildnachweise. Bekannte Abmessungen unterstützen die Kanalprüfung.

Das Workshopbild ist vorausgewählt. Einfache Regeln schlagen bis zu fünf passende
Alternativen vor; der restliche freigegebene Pool bleibt zugänglich. Die Auswahl
wird pro Beitrag gespeichert und kann über Kanäle und Runden wiederverwendet werden.
Die Anbindung löst Dateinamen in erreichbare Bilddaten oder URLs auf. Eine Datei im
Frontend-Projekt ist nicht automatisch für die serverseitige Veröffentlichung
erreichbar. Erreichbarkeit, Rechte und Kanalformat werden vor Versand geprüft.

## 8. Tracking und Buchungen

Links enthalten deterministisch zugewiesene Werte für Workshop, Kampagne, Runde,
Kanal und Variante sowie geeignete UTM-Parameter. Diese enthalten keine
Personennamen, E-Mail-Adressen oder anderen Teilnehmerdaten.

Die eigene Web-Anbindung erfasst einen Kampagnenbesuch und verknüpft ihn, soweit
möglich, mit dem vorhandenen Buchungsvorgang. Eine serverseitig bestätigte
Buchung wird einmalig gezählt; ein Checkout-Start oder die bloße Rückkehr von
einer Bezahlseite genügt nicht. Wiederholte Zahlungsbenachrichtigungen dürfen
keine weiteren Buchungen erzeugen. Stornierungen dürfen nicht als neue Buchungen
erscheinen. Bestehende PayPal-/Stripe-Zahlungslogik wird nicht neu aufgebaut.

Der bestätigte Standard-Zuordnungszeitraum beträgt **90 Tage rückwärts ab der
bestätigten Buchung**, bezogen auf den gebuchten Workshop. Es zählt der letzte
erfasste gültige Marketingklick für diesen Workshop, der höchstens 90 Tage vor
der Buchung liegt. Ein neuer gültiger Klick für denselben Workshop ersetzt den
bisherigen Kontakt; sein Zeitpunkt bestimmt die neue Frist. Kontakte für andere
Workshops werden nicht auf diese Buchung übertragen.

Ein späterer direkter Besuch überschreibt den Kontakt nicht und verlängert seine
Frist nicht. Ohne passenden Kontakt im Zeitraum bleibt die Herkunft unbekannt.
Die 90 Tage sind ein konfigurierbarer Startwert, begründet durch den Werbevorlauf
von 2–3 Monaten. Spätere Anpassungen werden anhand der beobachteten Abstände
zwischen Klick und Buchung bewertet, nicht automatisch vorgenommen.

Geräteübergreifende Vollständigkeit und kausale Aussagen über den Einfluss eines
Kanals sind nicht zugesagt. Automatische Linkvorschauen und wiederholte Aufrufe
können Klickzahlen beeinflussen.

Der Zuordnungszeitraum ist keine Festlegung der Datenspeicherdauer. Offen sind
Wiedererkennung, Einwilligung, Aufbewahrung und Löschung. Diese müssen vor
produktiver Besucherzuordnung festgelegt werden;
dieses Dokument trifft keine Aussage über eine datenschutzrechtliche Ausnahme.
Die eigene Buchungsseite erhält keine interne UTM-Markierung, die eine bereits
erfasste externe Herkunft überschreibt.

## 9. Fehler, Wiederholungen und doppelte Veröffentlichungen

Fehler sind pro Kanal sichtbar und unterscheidbar: fehlende Eingaben oder
Credentials, Generierungs-Timeout, Ablehnung, Schema- oder Faktenfehler,
Kanalfehler und unbekannter Ausgang. Freigaben sind nur für gültige Entwürfe möglich.

Vorübergehende Generierungs- und eindeutig wiederholbare API-Fehler können
begrenzt mit Warteabstand wiederholt werden. Permanente Fehler benötigen eine
sichtbare Korrektur. Eine automatische Reparatur eines Entwurfs erhält keine
automatische Freigabe. Die konkrete Zahl der Versuche wird später festgelegt.

Vor einem Versand wird eine stabile Operationskennung dauerhaft reserviert.
Gleichzeitige Klicks dürfen denselben Versand nicht mehrfach starten. Native
Idempotenz eines Anbieters wird verwendet, wenn verfügbar. Externe Beitrags-IDs
werden für Aktualisierungen gespeichert. Eine neue Fassung ist keine automatische
Erlaubnis, einen bereits veröffentlichten Beitrag noch einmal anzulegen.

Bricht die Verbindung nach dem Versand ab, kann ein Beitrag schon existieren.
Dann lautet der Zustand zunächst „Ausgang unbekannt“. Abgleich mit dem Anbieter
oder menschliche Klärung geht einem erneuten Erstellen voraus. Eine lokale
Kennung allein garantiert keine einmalige externe Veröffentlichung.

## 10. Evaluation und Tests

Ein erster Referenzsatz umfasst synthetische oder anonymisierte Kinder-Yoga-,
Eltern-Kind-Yoga-, Wellness-, Meditations-, Stressabbau- und Massage-Workshops.
Erwartete Kanaleignung und verbindliche Fakten gehören zu jedem Fall.
Zusätzliche Fälle prüfen fehlende Angaben, widersprüchliche Texte, abgelaufene
Frühbucherfristen, Terminänderungen, Prompt Injection und unzulässige Zielgruppen.

Deterministische Prüfungen decken Pflichtfelder, Schemas, Zeichenlimits, bekannte
Fakten, Preisfristen, Bildreferenzen und Links ab. Kritische Fehler dürfen nicht
durch einen guten Durchschnittswert verdeckt werden. Auf dem vereinbarten
Referenzsatz müssen die harten Prüfungen bestehen; dies ist kein Beweis für
Fehlerfreiheit beliebiger freier Texte.

Die qualitative Bewertung erfasst Ton, Zielgruppe, Kanaleignung, Titel,
Handlungsaufforderung, Redundanz und unbelegte Aussagen. Eine kleine menschlich
bewertete Referenz dient als Grundlage; ein LLM-Bewerter kann später ergänzen.
Bewertungen verwenden vorab definierte Kriterien und Beispiele. Konkrete
Bewertungsschwellen sind noch gemeinsam festzulegen.

Vergleiche halten Fixtures, Prüfkriterien und Generierungszeitpunkt kontrolliert
und protokollieren Prompt-/Schema-/Modellversion, Ergebnisse, Laufzeit und
verfügbare Nutzungsdaten. Live-Generierungen können variieren; Reproduzierbarkeit
bedeutet dokumentierte Bedingungen und auswertbare gespeicherte Resultate.

pytest ist als Testwerkzeug vorgesehen. Routinetests und CI verwenden Fakes oder
gespeicherte synthetische Antworten und benötigen keine produktiven Credentials.
Live-Evaluation ist ein eigener bewusster Lauf. Integrationstests prüfen besonders
Freigabeentwertung, Teilfehler, konkurrierende Aktionen, unklaren Versandstatus
und wiederholte Zahlungsbenachrichtigungen.

## 11. Sicherheit und Betrieb

- Alle Schreibaktionen prüfen Benutzer und bestehende Workshop-Berechtigungen
  serverseitig. Lehrer benötigen keine zusätzliche Adminfreigabe für die Workshops,
  die sie betreuen dürfen; der genaue vorhandene Berechtigungsumfang ist zu prüfen.
- Generieren und Freigeben/Veröffentlichen sind getrennte Aktionen. Ein vom
  Browser gesendeter Freigabestatus ist kein Berechtigungsnachweis.
- Produktive Schlüssel, OAuth-Tokens und Firebase-Credentials bleiben serverseitig.
  Eine spätere `.env.example` enthält ausschließlich Platzhalter.
- Privilegierte Firebase-Zugriffe brauchen eigene Autorisierungsprüfungen;
  Frontend-Sichtbarkeit ist keine Zugriffskontrolle.
- Logs enthalten Kennungen, Status, Fehlerklassen und nötige Laufzeitdaten.
  Secrets, vollständige Zahlungspayloads und Teilnehmerdaten werden nicht geloggt.
- Öffentliche Fixtures und Screenshots verwenden synthetische oder anonymisierte
  Daten und keine privaten Projektkonfigurationen.
- Workshoptexte steuern keine Werkzeuge, Netzwerkziele oder Secrets. Buchungs- und
  Bildadressen stammen aus geprüfter Konfiguration beziehungsweise Integrationsdaten.
- Pro Kanal werden erforderliche Bildfreigaben und Darstellungsvorgaben beachtet.

## 12. Offene Punkte und der Zeitpunkt ihrer Klärung

Der Dokumentations-PR hält die gemeinsame Grundlage und ihre offenen Punkte fest.
Diese Punkte werden jeweils vor der davon abhängigen Umsetzung entschieden oder
geprüft. Die kleine Python-Paketgrundlage kann unabhängig von Plattformzugängen,
Tracking und produktiver Firebase-Anbindung entstehen.

| Punkt | Nächster Schritt | Erforderlich vor |
| --- | --- | --- |
| Python-Laufzeit | Unterstützte Python-Version für Paket und geplante Firebase-Laufzeit auswählen | Festlegung der Paketgrundlage im ersten Implementierungs-Issue |
| Workshop-Datenmodell | Ein anonymisiertes Beispiel aus der bestehenden App prüfen, einschließlich Frühbucherfristen, Altersangaben und Zeitzonen | Festlegung der öffentlichen Workshop-Datenmodelle |
| Konkretes Datenmapping | Feldnamen, Versionierung, Fristsemantik, Buchungsstatus, Berechtigungen und Bildpfade in der bestehenden App prüfen | Umsetzung der jeweiligen Firebase-Integration |
| Ausführung | Vorschlag: kurze, getrennte serverseitige Aktionen pro Kanal; Zeitbedarf messen und Verhalten bei Abbruch/Schließen der App festlegen | Anbindung der Generierung an die Lehrer-App und Zusage von Hintergrundausführung |
| Tracking | 90 Tage mit Workshopbindung sind entschieden; Identifikation, Einwilligung, Aufbewahrung, Löschung und Definition der gezählten Buchung noch festlegen | Umsetzung der personenbezogenen Besucherzuordnung und ihrer Speicherung |
| Evaluation | Bewertungsrubrik, Qualitätsgrenze und tolerierten Korrekturaufwand konkretisieren | Bewertung der ersten generierten Texte und Vergleich von Promptständen |
| Google-Zugang | Owner-Zugriff ist vorhanden; separate API-Genehmigung, OAuth und nutzbare Profilstandorte prüfen; Zugang frühzeitig beantragen | Live-Prüfung und Abnahme des Google-Publishing-Adapters |
| Andere Kanäle | Konkrete Pflichtfelder und Bildvorgaben prüfen; Anbieterzugänge einrichten und Eintragungszeit messen | Feldregeln vor dem jeweiligen Kanalentwurf; Zugänge und Zeitmessung vor dessen Pilotabnahme |
| Modell | Modell und Versionsbindung anhand des ersten Generierungsfalls auswählen | Erster Live-Generierung und Vergleichsevaluation |

Eine offene Integration wird nicht als vorhandene Funktion dokumentiert. Änderungen
am bestätigten Scope werden mit dem Nutzer entschieden; normale Details innerhalb
eines freigegebenen Issues werden ohne neue Grundsatzdiskussion gelöst.
