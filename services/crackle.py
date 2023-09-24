"""
Credit to rlaphoenix for the title storage

CRACKLE
Author: stabbedbybrick

Info:

"""

import subprocess
import json
import shutil
import sys
import base64

from urllib.parse import urlparse
from pathlib import Path
from collections import Counter

import click
import httpx

from bs4 import BeautifulSoup
from rich.console import Console

from helpers.utilities import (
    info,
    string_cleaning,
    set_save_path,
    print_info,
    set_filename,
)
from helpers.cdm import local_cdm, remote_cdm
from helpers.titles import Episode, Series, Movie, Movies
from helpers.args import Options, get_args


class CRACKLE:
    def __init__(self, config, **kwargs) -> None:
        self.config = config
        self.tmp = Path("tmp")
        self.url = kwargs.get("url")
        self.quality = kwargs.get("quality")
        self.remote = kwargs.get("remote")
        self.titles = kwargs.get("titles")
        self.info = kwargs.get("info")
        self.episode = kwargs.get("episode")
        self.season = kwargs.get("season")
        self.movie = kwargs.get("movie")
        self.complete = kwargs.get("complete")
        self.all_audio = kwargs.get("all_audio")

        self.console = Console()
        self.api = "https://prod-api.crackle.com"
        self.client = httpx.Client(
            headers={
                "user-agent": "Chrome/113.0.0.0 Safari/537.36",
                "x-crackle-platform": "5FE67CCA-069A-42C6-A20F-4B47A8054D46",
            },
            follow_redirects=True,
        )

        self.tmp.mkdir(parents=True, exist_ok=True)

        self.episode = self.episode.upper() if self.episode else None
        self.season = self.season.upper() if self.season else None
        self.quality = self.quality.rstrip("p") if self.quality else None

        self.get_options()

    def get_data(self, url: str) -> json:
        self.video_id = urlparse(url).path.split("/")[2]

        r = self.client.get(f"{self.api}/content/{self.video_id}")
        if not r.is_success:
            print(f"\nError! {r.status_code}\n{r.json()['error']['message']}")
            shutil.rmtree(self.tmp)
            sys.exit(1)

        return r.json()["data"]

    def get_series(self, url: str) -> Series:
        data = self.get_data(url)

        r = self.client.get(f"{self.api}/content/{self.video_id}/children").json()

        seasons = [
            self.client.get(f"{self.api}/content/{x['id']}/children").json()
            for x in r["data"]
        ]

        return Series(
            [
                Episode(
                    id_=None,
                    service="CRKL",
                    title=data["metadata"][0]["title"],
                    season=int(episode["seasonNumber"]),
                    number=int(episode["episodeNumber"]),
                    name=episode["title"],
                    year=None,
                    data=episode["id"],
                    description=episode.get("shortDescription"),
                )
                for season in seasons
                for episode in season["data"]
            ]
        )

    def get_movies(self, url: str) -> Movies:
        data = self.get_data(url)

        r = self.client.get(f"{self.api}/content/{self.video_id}/children").json()

        return Movies(
            [
                Movie(
                    id_=None,
                    service="CRKL",
                    title=data["metadata"][0]["title"],
                    year=data["metadata"][0]["releaseDate"].split("-")[0]
                    if data["metadata"][0]["releaseDate"] is not None
                    else None,
                    name=data["metadata"][0]["title"],
                    data=r["data"][0]["id"],
                    synopsis=data["metadata"][0].get("longDescription"),
                )
            ]
        )

    def get_playlist(self, id: str) -> tuple:
        r = self.client.get(f"{self.api}/playback/vod/{id}").json()

        manifest = [
            source["url"].replace("session", "dash")
            for source in r["data"]["streams"]
            if source.get("type") == "dash-widevine"
        ][0]

        lic_url = [
            source["drm"]["keyUrl"]
            for source in r["data"]["streams"]
            if source.get("type") == "dash-widevine"
        ][0]

        return lic_url, manifest

    def get_pssh(self, soup: str) -> str:
        kid = (
            soup.select_one("ContentProtection")
            .attrs.get("cenc:default_KID")
            .replace("-", "")
        )
        array_of_bytes = bytearray(b"\x00\x00\x002pssh\x00\x00\x00\x00")
        array_of_bytes.extend(bytes.fromhex("edef8ba979d64acea3c827dcd51d21ed"))
        array_of_bytes.extend(b"\x00\x00\x00\x12\x12\x10")
        array_of_bytes.extend(bytes.fromhex(kid.replace("-", "")))
        return base64.b64encode(bytes.fromhex(array_of_bytes.hex())).decode("utf-8")

    def get_mediainfo(self, manifest: str, quality: str) -> str:
        soup = BeautifulSoup(self.client.get(manifest), "xml")
        new_manifest = soup.select_one("BaseURL").text + "index.mpd"
        self.soup = BeautifulSoup(self.client.get(new_manifest), "xml")
        pssh = self.get_pssh(self.soup)
        elements = self.soup.find_all("Representation")
        heights = sorted(
            [int(x.attrs["height"]) for x in elements if x.attrs.get("height")],
            reverse=True,
        )

        if quality is not None:
            if int(quality) in heights:
                return quality, pssh
            else:
                closest_match = min(heights, key=lambda x: abs(int(x) - int(quality)))
                info(f"Resolution not available. Getting closest match:")
                return closest_match, pssh

        return heights[0], pssh

    def get_content(self, url: str) -> object:
        if self.movie:
            with self.console.status("Fetching titles..."):
                content = self.get_movies(self.url)
                title = string_cleaning(str(content))

            info(f"{str(content)}\n")

        else:
            with self.console.status("Fetching titles..."):
                content = self.get_series(url)
                for episode in content:
                    episode.name = episode.get_filename()

                title = string_cleaning(str(content))
                seasons = Counter(x.season for x in content)
                num_seasons = len(seasons)
                num_episodes = sum(seasons.values())

            info(
                f"{str(content)}: {num_seasons} Season(s), {num_episodes} Episode(s)\n"
            )

        return content, title

    def get_options(self) -> None:
        opt = Options(self)
        content, title = self.get_content(self.url)

        if self.episode:
            downloads = opt.get_episode(content)
        if self.season:
            downloads = opt.get_season(content)
        if self.complete:
            downloads = opt.get_complete(content)
        if self.movie:
            downloads = opt.get_movie(content)
        if self.titles:
            opt.list_titles(content)

        for download in downloads:
            self.download(download, title)

    def download(self, stream: object, title: str) -> None:
        with self.console.status("Getting media info..."):
            lic_url, manifest = self.get_playlist(stream.data)
            res, pssh = self.get_mediainfo(manifest, self.quality)

        with self.console.status("Getting decryption keys..."):
            keys = (
                remote_cdm(pssh, lic_url, self.client)
                if self.remote
                else local_cdm(pssh, lic_url, self.client)
            )
            with open(self.tmp / "keys.txt", "w") as file:
                file.write("\n".join(keys))

        self.filename = set_filename(self, stream, res, audio="AAC2.0")
        self.save_path = set_save_path(stream, self.config, title)
        self.manifest = manifest
        self.key_file = self.tmp / "keys.txt"
        self.sub_path = None

        if self.info:
            print_info(self, stream, keys)

        info(f"{stream.name}")
        for key in keys:
            info(f"{key}")
        click.echo("")

        args, file_path = get_args(self, res)

        if not file_path.exists():
            try:
                subprocess.run(args, check=True)
            except:
                raise ValueError("Download failed or was interrupted")
        else:
            info(f"{self.filename} already exist. Skipping download\n")
            self.sub_path.unlink() if self.sub_path.exists() else None
            pass