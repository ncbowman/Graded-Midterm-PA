# MIS Music Survey — TA Table Playlist

## What this program does

This Python program reads a music-listening survey CSV, cleans inconsistent or invalid entries, and displays playlist statistics through a menu.

The menu includes:
1. Top song
2. Top artist
3. Top genre
4. Total minutes listened
5. Garthle, a guess-the-artist game that uses TheAudioDB API
6. A signature report called **Listening Personality**
7. Quit

## Files in this repository

- `music_survey.py` — the Python program
- `listening_survey.csv` — the survey data file
- `requirements.txt` — the Python packages needed

## Requirements

- Python 3
- Internet connection for Garthle
- The packages listed in `requirements.txt` (`polars` and `requests`)

## Setup

Open a terminal in the folder containing these files and run:

```bash
python -m pip install -r requirements.txt
```

## Run the program

Make sure `music_survey.py` and `listening_survey.csv` are in the same folder. Run:

```bash
python music_survey.py
```

To use a different CSV with the same required column format, provide its path:

```bash
python music_survey.py path/to/another_survey.csv
```

The CSV must contain these columns: `ts`, `track_name`, `artist_name`, `ms_played`, `type`, and `genre`.

## Signature report: Listening Personality

The signature report goes beyond the required top-song, top-artist, top-genre, and minutes-listened statistics. It analyzes when music is played, identifies the busiest listening hour, compares artist loyalty with listening variety, assigns a simple listening-personality label (such as Night owl or Early bird), and reports the longest single listen. This gives the TA table a quick picture of the group's listening habits, not just its most popular songs.

## Data-cleaning decisions

The program normalizes text for grouping, handles missing or invalid values, removes duplicate records, standardizes common genre variations, and excludes plays shorter than 30 seconds from music listening statistics. It also rejects negative durations and implausibly long single-song plays. Podcast records are kept separate from music-only statistics where appropriate. These are judgment calls for this survey, rather than universal rules for every streaming dataset.

## Garthle and the API

Garthle looks up artists through TheAudioDB's public search endpoint. It needs an internet connection. If the API cannot find an artist or the connection fails, the program asks for another guess or explains that the game could not start. Install `requests` with the requirements command above.
