# V1-Scope: workshop-marketing-agent

Stand: 17. September 2026. Status: **Entwurf zur gemeinsamen Prüfung**.

Dieses Dokument bündelt die im Architekturgespräch bestätigten Anforderungen.
Die vorgeschlagene technische Ausgestaltung steht in [architecture.md](architecture.md).
Offene Details sind dort separat aufgeführt. V1 ist noch nicht implementiert.

## 1. Produktziel

Bestehende Workshops eines Yogastudios sollen zuverlässig und mit wenig aktiver
Arbeit beworben werden. Es gibt ungefähr 1–2 Workshops pro Monat und 2–3 Lehrer.
Die Werbung beginnt 2–3 Monate vor dem Workshop. Aktuell stammen die Teilnehmer
vor allem aus dem Kreis gegenwärtiger oder ehemaliger Studiomitglieder.

Das Projekt soll zugleich ein öffentliches Python-Portfolio mit klarer Architektur,
kontrollierter LLM-Nutzung, Evaluation, Tests und echter Anwendungsintegration sein.
Es belegt zunächst Applied-LLM- und Software-Engineering; eigenes Modelltraining
gehört nicht zum V1-Ziel.

## 2. Bestätigte Entscheidungen

- Python-Package statt TypeScript-/npm-Package; kein eigenständiger Ersatz für die
  Lehrer-App. Die bestehende Weboberfläche kann ihre Sprache beibehalten.
- Workshopdaten einschließlich Frühbucherinformationen kommen aus Firestore.
  Die Integration mappt sie in das unabhängige Datenmodell des Packages.
- Der ausführliche Originaltext bleibt ohne ausdrücklich angestoßene Überarbeitung
  bestehen. Kanaltexte werden daraus herzlich, verständlich und passend abgeleitet.
- Widersprüche zwischen strukturierten Daten und Text müssen vor Veröffentlichung
  der betroffenen Inhalte geklärt werden.
- Lehrer können die ihnen zugänglichen Workshops vollständig betreuen und Beiträge
  selbst freigeben; eine zusätzliche Adminfreigabe ist nicht erforderlich.
- Texte können direkt bearbeitet oder über einen kurzen Änderungswunsch per LLM
  überarbeitet werden. Einzelne Kanäle lassen sich neu generieren oder deaktivieren.
- Ein Vorschlag wird vorausgewählt; weitere Varianten sind optional bis zu drei
  Vorschlägen insgesamt. Die aktuelle Auswahl wird ausdrücklich freigegeben.
- Mehrere Werberunden sowie Aktualisierungen sind vorgesehen. Erinnerungen werden
  in V1 von Lehrern angestoßen, nicht automatisch terminiert.
- Bestehende Workshop- und Webseitenbilder bilden einen gemeinsamen Bildpool.
  Das Workshopbild ist die Vorauswahl; passende Alternativen können gewählt werden.
- Links, erfasste Klicks und bestätigte Buchungen sollen zugeordnet werden können.
  Es gilt der letzte erfasste gültige Marketingklick für den gebuchten Workshop
  innerhalb von 90 Tagen vor der bestätigten Buchung; sonst bleibt die Herkunft
  unbekannt. Der Zeitraum ist als Standard konfigurierbar.

## 3. Kanäle

| Kanal | Rolle in V1 | Veröffentlichungsweg |
| --- | --- | --- |
| Eigene Workshopseite | Verbindliche Information und Buchungsziel; bestehende Veröffentlichung anbinden | Über die vorhandene App; keine automatische Ersetzung des Originaltexts |
| Google Business Profile | Erster externer automatischer Kanal | Offizielle API nach Freigabe; Zugang ist Voraussetzung |
| Rausgegangen | Kostenloser Eintrag mit eigener Buchungsadresse | Vorbereitete Felder, Eintragen im Anbieterportal, Statusbestätigung |
| HIMBEER / Berlin mit Kind | Kostenlose Kurseinträge für passende Kinder-, Eltern- und Familienangebote | Vorbereitete Felder für das Kursverzeichnis, Statusbestätigung |

Die Auswahl erfolgt pro Workshop. Nicht jeder Workshop gehört auf jeden Kanal.
Neue Erinnerungen erzeugen nicht automatisch doppelte Kalendereinträge. Bestehende
Einträge werden aktualisiert, wenn das der passende unterstützte Ablauf ist.

**Verifizierte Voraussetzungen und Grenzen:**

- Google unterstützt [Erstellen und Bearbeiten von Beiträgen](https://developers.google.com/my-business/content/posts-data).
  Der Studio-Owner-Zugriff ist bestätigt; die separate
  [API-Zulassung](https://developers.google.com/my-business/content/prereqs) ist noch offen.
- Rausgegangen bestätigt [kostenlose Event-Einträge](https://zentrale.rausgegangen.de/)
  und [externe Buchungslinks](https://rausgegangen-assist.freshdesk.com/support/solutions/articles/44002486114-kann-ich-einen-externen-ticketlink-hinterlegen-).
  Das eigene Ticketing der Plattform ist für V1 nicht erforderlich.
- HIMBEER beschreibt [kostenlose Kurseinträge und einmalige Workshops](https://berlinmitkind.de/anleitung-kurse/).
  Das Kursverzeichnis ist vom redaktionellen Veranstaltungskalender zu unterscheiden.
  Die Anleitung sieht für Kurseinträge keine eingebetteten Bilder vor.
- Für Rausgegangen und HIMBEER wurde bislang keine passende öffentliche offizielle
  Publishing-API verifiziert. Automatisches Veröffentlichen wird nicht zugesagt.

Quellen für die Kanalauswahl wurden am 16.–17. September 2026 geprüft.
Plattformbedingungen und technische Fähigkeiten müssen vor Integration erneut
gegen die dann aktuellen offiziellen Informationen geprüft werden.

## 4. Nutzerablauf

1. Lehrer öffnet einen vorhandenen Workshop und startet „Marketing erstellen“.
2. Das System prüft die Eingangsdaten und empfiehlt passende Kanäle mit Begründung.
3. Je Kanal erscheint ein geprüfter Entwurf mit geeignetem Veröffentlichungsweg,
   fertigem Buchungslink und gegebenenfalls Bildvorschlag.
4. Lehrer kann Text direkt bearbeiten, einen Änderungswunsch eingeben, eine
   Variante oder ein anderes Bild wählen und Kanäle deaktivieren.
5. Nach Prüfung wird die konkrete Fassung freigegeben.
6. Google veröffentlicht automatisch; für andere Kanäle werden Felder kopiert und
   Einreichung beziehungsweise Veröffentlichung anschließend bestätigt.
7. Die Übersicht zeigt je Kanal Zeiten, Fehler, Wiederholungsmöglichkeiten und
   die zuletzt veröffentlichte Fassung beziehungsweise den öffentlichen Link.
8. Spätere Werberunden und Aktualisierungen beginnen bei den aktuellen Fakten und
   bewahren die Historie bisheriger Veröffentlichungen.

Eine Anfrage an eine Redaktion oder ein abgesendetes Formular ist nicht automatisch
eine Veröffentlichung. Der Status darf den tatsächlichen Kenntnisstand nicht übertreiben.

## 5. Bilder und Inhalte

Dateinamen der Workshopbilder stehen bereits in Firestore. Die Dateien liegen in
Ordnern der Lehrer-App und der Webseite. Der Bildpool katalogisiert geeignete
vorhandene Bilder und ihre erlaubte Nutzung; eine Kopie aller Dateien ist nicht nötig.

Themen wie Yoga, Meditation, Wellness, Kinder, Eltern-Kind, Massage und Studio
helfen bei der Auswahl. Dasselbe Bild darf für mehrere Workshops verwendet werden,
soweit seine Freigabe das erlaubt. Kanäle ohne Bildanforderung bleiben textbasiert.

Beispielhafte Änderungswünsche: „Bitte kürzer“, „Herzlicher formulieren“ oder
„Betone, dass man auch alleine teilnehmen kann“, sofern diese Aussage belegt ist.
Neue Fakten und stärkere Wirkversprechen dürfen dabei nicht entstehen.

## 6. Tracking

V1 umfasst kanalspezifische Links, eine einfache Klickerfassung und die Zuordnung
zu vorhandenen bestätigten Buchungen. Die Webseite verwendet bereits Firebase
Cloud Functions mit PayPal und Stripe. Diese Zahlungsabwicklung bleibt Grundlage.

Benötigt werden mindestens Workshop-, Kampagnen-, Runden-, Kanal- und Variantenbezug
sowie UTM-Werte. Die bestätigte Zuordnungsregel lautet:

- Letzter gültiger Marketingklick für denselben Workshop, höchstens 90 Tage vor
  der bestätigten Buchung; gerechnet ab der Buchung, nicht ab dem Workshoptermin.
- Ein neuer gültiger Marketingklick für diesen Workshop ersetzt den vorherigen
  und setzt eine neue Frist ab seinem Klickzeitpunkt.
- Direkte Besuche überschreiben die Herkunft nicht und verlängern die Frist nicht.
- Klicks für andere Workshops werden nicht übertragen. Ohne passenden Kontakt
  bleibt die Herkunft unbekannt.

Die 90 Tage sind ein konfigurierbarer Startwert passend zum Werbevorlauf von
2–3 Monaten. Später kann der Wert anhand beobachteter Buchungsabstände überprüft
werden. Wiedererkennung, Datenspeicherung und Einwilligung werden vor produktiver
Erfassung konkretisiert; die 90 Tage legen keine Speicherdauer fest.
V1 braucht eine prüfbare Auswertung, aber kein eigenes Analytics-Dashboard.

## 7. Erfolg und Abnahme

**Bestätigte Zeitziele nach einmaliger Einrichtung:**

- Ungefähr eine Minute aktive Arbeit pro automatisch veröffentlichbarem Kanal.
- Maximal ungefähr drei Minuten pro Kanal mit manuellem Formulareintrag.
- Maximal zehn Minuten aktive Arbeit für eine vollständige Werberunde eines
  Workshops über alle ausgewählten Kanäle, einschließlich üblicher Textkorrekturen.

Rechenzeit und Wartezeit externer Plattformen werden gesondert gemessen. Diese
Werte sind Pilotziele, keine bereits nachgewiesenen Leistungsangaben.
Texte sollen kaum Korrekturen benötigen. Mehr Teilnehmer sind das Geschäftsziel;
bei wenigen Workshops ist die Entwicklung der Buchungen nur vorsichtig bewertbar.

**Vorgeschlagene überprüfbare Abnahmekriterien:**

- Referenzworkshops durchlaufen Datenprüfung, Kanalauswahl, Generierung und Review.
- Pflichtfelder, Formate, bekannte Fakten und Preisfristen bestehen die vereinbarten
  harten Prüfungen; konflikthafte Ergebnisse können nicht veröffentlicht werden.
- Direkte Bearbeitung, LLM-Überarbeitung, Bildwechsel und Kanaldeaktivierung funktionieren.
- Google kann mit freigeschaltetem Zugang einen freigegebenen Beitrag erstellen
  und aktualisieren. Der manuelle Ausweichweg ersetzt dieses Integrationsziel nicht.
- Rausgegangen- und HIMBEER-Einträge lassen sich vorbereiten, bestätigen und aktualisieren;
  die tatsächliche Eintragungszeit wird mit passenden Workshops gemessen.
- Teilfehler erhalten erfolgreiche Ergebnisse. Doppelklicks und Wiederholungen
  verursachen keine unkontrollierten zusätzlichen Beiträge.
- Mindestens ein kontrollierter Buchungsdurchlauf prüft die Zuordnung und die
  einmalige Zählung trotz wiederholter Zahlungsbenachrichtigungen.
- Gezielte Zuordnungsfälle prüfen die 90-Tage-Grenze, einen neueren Marketingklick,
  einen direkten Folgebesuch und Klicks für einen anderen Workshop.
- Eine kleine qualitative Referenzbewertung und ein dokumentierter Vergleich
  zweier Promptstände zeigen, wie Änderungen beurteilt werden.
- Der Pilot wird in der bestehenden Lehrer-App durchgeführt. Fehlende Zugänge
  oder nicht getestete Funktionen werden als offen dokumentiert.

Konkrete Qualitätsgrenzen und der Umfang der Pilotstichprobe sind noch festzulegen.

## 8. Später oder außerhalb von V1

- Kindaling: ausdrücklich auf später verschoben; Gebühren und separate Buchungswege
  müssten vor Aufnahme geklärt werden.
- visitBerlin: mögliche spätere kostenlose Ergänzung.
- nebenan.de, Berlin.de, Instagram, Facebook und weitere Plattformen: keine
  verbindlichen V1-Integrationen.
- Facebook-Gruppen bleiben bei einer späteren Aufnahme manuell; keine Bots.
- Zeitgesteuerte Erinnerungen, eigenständige Veröffentlichung ohne Freigabe,
  autonome Browsersteuerung und Umgehung fehlender APIs.
- Neue Workshopideen, Reels, Videos und fortlaufende neue Bildproduktion.
- Paid Ads, Budgetoptimierung, automatisches Lernen aus Buchungen und großes Dashboard.
- Große SaaS-Plattform, zusätzliche Mandantenverwaltung und Agent-Frameworks.

Die Architektur berücksichtigt spätere Erweiterungen durch kleine Kanaladapter,
ohne diese Erweiterungen bereits in V1 zu implementieren.

## 9. Entwicklung und Dokumentation

Zunächst werden diese Architektur- und Scope-Entwürfe gemeinsam geprüft und die
verbindliche Grundlage im ersten Dokumentations-PR festgehalten. Offene Punkte
werden entschieden oder ausdrücklich einem späteren Issue zugeordnet, bevor die
jeweils davon abhängige Umsetzung beginnt.

Die Entwicklung folgt einzelnen Issues mit klarer Abnahme, kleinen Änderungen,
passenden Tests, Nutzerreview, Pull Request und Merge. Review-, Zustands- und
Evaluationsregeln werden vor dem ersten vollständigen Generierungsablauf konkretisiert.
Die Firebase-Anbindung wird früh mit einem kleinen Datenbeispiel geprüft, damit
Integrationsannahmen nicht erst am Ende auffallen.

README und weitere Dokumentation werden mit tatsächlichen Funktionen aufgebaut.
Wenige begründete Architekturentscheidungen können später als ADRs ergänzt werden.
Dieses Dokument ist keine Aufforderung, V1 in einem einzigen Schritt zu erzeugen.
