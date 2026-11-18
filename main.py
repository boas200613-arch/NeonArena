from direct.showbase.ShowBase import ShowBase
from direct.gui.DirectGui import (
    DirectButton, DirectFrame, DirectLabel, DirectSlider, DirectEntry
)
from panda3d.core import (
    loadPrcFileData, AmbientLight, DirectionalLight,
    Vec4, Vec3, WindowProperties
)
import math
import random
import os
import wave
import struct
import json
import asyncio
import threading
import queue
import urllib.request
import urllib.error
from datetime import date


loadPrcFileData("", "window-title NeonArena v1.0 - Online")
loadPrcFileData("", "win-size 1280 720")
loadPrcFileData("", "sync-video true")

SAVE_FILE = "neonarena_save.json"


class NeonArena(ShowBase):

    def __init__(self):
        super().__init__()

        self.disableMouse()
        self.setBackgroundColor(0.015, 0.02, 0.07, 1)

        self.state = "menu"
        self.settings_return_state = "menu"

        self.level = 1
        self.max_level = 25
        self.score = 0
        self.run_score = 0
        self.collected_orbs = 0
        self.level_orbs = 12

        self.energy_points = 0
        self.highest_level = 1
        self.player_name = "PLAYER"
        self.leaderboard = []
        self.owned_abilities = []

        self.last_daily_reward = ""
        self.total_orbs = 0
        self.total_energy_earned = 0
        self.games_played = 0
        self.total_playtime = 0
        self.highest_combo = 0

        self.last_playtime_save = 0

        self.sensitivity = 0.15
        self.music_volume = 0.35
        self.effects_volume = 0.7

        self.speed_boost = 0
        self.shield = False
        self.combo = 0
        self.last_pickup = -100
        self.run_recorded = False
        self.second_chance_used = False

        self.keys = {}
        self.orbs = []
        self.bots = []
        self.powerups = []

        self.center_x = 640
        self.center_y = 360
        self.mouse_active = False
        self.mouse_initialized = False

        self.music = None
        self.pickup_sound = None

        # ONLINE MULTIPLAYER
        self.server_url = "wss://neonarena-server.onrender.com/game"
        self.net_running = False
        self.net_thread = None
        self.net_out = queue.Queue()
        self.net_in = queue.Queue()
        self.net_connected = False
        self.net_player_id = None
        self.net_room = ""
        self.remote_players = {}
        self.net_send_timer = 0.0
        self.multiplayer_status = "OFFLINE"

        # ACCOUNT AND ONLINE LEADERBOARD
        self.api_base_url = "https://neonarena-server.onrender.com"
        self.account_token = ""
        self.account_username = ""
        self.account_best_score = 0
        self.account_best_level = 1
        self.account_rank = 0
        self.online_leaderboard = []
        self.auth_busy = False

        self.load_save()

        self.accept("escape", self.escape)
        self.accept("mouse1", self.capture_mouse)

        key_mapping = {
            "z": ["z", "w"],
            "q": ["q", "a"],
            "s": ["s"],
            "d": ["d"],
            "space": ["space"]
        }

        for action, keys in key_mapping.items():
            for key in keys:
                self.accept(key, self.set_key, [action, True])
                self.accept(key + "-up", self.set_key, [action, False])

        self.setup_lighting()
        self.create_hud()
        self.create_audio()
        self.create_level(1)

        self.show_menu()
        self.taskMgr.add(self.update, "game-update")

    # SAVE SYSTEM

    def load_save(self):
        if not os.path.exists(SAVE_FILE):
            return

        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)

            self.energy_points = int(data.get("energy_points", 0))
            self.highest_level = int(data.get("highest_level", 1))
            self.player_name = str(data.get("player_name", "PLAYER"))[:16]
            self.leaderboard = data.get("leaderboard", [])
            self.owned_abilities = data.get("owned_abilities", [])

            self.last_daily_reward = data.get("last_daily_reward", "")
            self.total_orbs = int(data.get("total_orbs", 0))
            self.total_energy_earned = int(data.get("total_energy_earned", 0))
            self.games_played = int(data.get("games_played", 0))
            self.total_playtime = float(data.get("total_playtime", 0))
            self.highest_combo = int(data.get("highest_combo", 0))

        except Exception as error:
            print("Save file error:", error)

    def save_game(self):
        data = {
            "energy_points": self.energy_points,
            "highest_level": self.highest_level,
            "player_name": self.player_name,
            "leaderboard": self.leaderboard[:10],
            "owned_abilities": self.owned_abilities,
            "last_daily_reward": self.last_daily_reward,
            "total_orbs": self.total_orbs,
            "total_energy_earned": self.total_energy_earned,
            "games_played": self.games_played,
            "total_playtime": self.total_playtime,
            "highest_combo": self.highest_combo
        }

        try:
            with open(SAVE_FILE, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4)
        except Exception as error:
            print("Saving error:", error)

    def add_energy_points(self, amount):
        amount = int(amount)
        self.energy_points += amount
        self.total_energy_earned += amount
        self.save_game()
        self.update_hud()

    # DAILY REWARD

    def can_claim_daily(self):
        return self.last_daily_reward != date.today().isoformat()

    def claim_daily_reward(self):
        if not self.can_claim_daily():
            self.show_message("DAILY REWARD ALREADY CLAIMED!")
            return

        reward = 100
        self.last_daily_reward = date.today().isoformat()
        self.add_energy_points(reward)
        self.show_menu()

    # LEADERBOARD

    def record_result(self):
        if self.run_recorded:
            return

        self.run_recorded = True

        if self.run_score <= 0:
            return

        self.leaderboard.append({
            "name": self.player_name,
            "score": self.run_score,
            "level": self.level
        })

        self.leaderboard.sort(
            key=lambda item: item.get("score", 0),
            reverse=True
        )

        self.leaderboard = self.leaderboard[:10]
        self.save_game()
        if self.account_token:
            threading.Thread(
                target=self._submit_score_worker,
                args=(int(self.run_score), int(self.level)),
                daemon=True
            ).start()

    def _submit_score_worker(self, score, level):
        try:
            payload = json.dumps({"score": score, "level": level}).encode("utf-8")
            request = urllib.request.Request(
                self.api_base_url + "/scores", data=payload,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.account_token},
                method="POST"
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.net_in.put({"type": "score_result", "data": data})
        except Exception as error:
            self.net_in.put({"type": "score_error", "message": str(error)[:120]})

    def set_player_name(self):
        if hasattr(self, "name_entry"):
            name = self.name_entry.get().strip()

            if name:
                self.player_name = name[:16].upper()
                self.save_game()

    def show_leaderboard(self, fetch=True):
        self.state = "leaderboard"
        self.clear_menu()
        self.hide_game()

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.98),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        self.make_label(
            self.menu, "ONLINE LEADERBOARD",
            0.82, 0.12, (0.1, 0.85, 1, 1)
        )

        if self.account_token:
            self.make_label(
                self.menu,
                f"SIGNED IN: {self.account_username} | PERSONAL BEST: {self.account_best_score} | RANK: #{self.account_rank or '-'}",
                0.62, 0.035, (0.9, 0.95, 1, 1)
            )
        else:
            self.make_label(
                self.menu, "LOG IN TO SAVE YOUR PERSONAL BEST",
                0.62, 0.04, (1, 0.75, 0.25, 1)
            )

        entries = self.online_leaderboard if self.online_leaderboard else self.leaderboard
        if not entries:
            self.make_label(
                self.menu, "Loading online scores..." if self.account_token else "No online scores yet.",
                0.20, 0.045, (0.8, 0.9, 1, 1)
            )
        else:
            for index, entry in enumerate(entries[:10]):
                z = 0.43 - index * 0.105
                line = (
                    f"{index + 1}. {entry.get('name', 'PLAYER')}  "
                    f"SCORE: {entry.get('score', 0)}  "
                    f"LEVEL: {entry.get('level', 1)}"
                )
                self.make_label(self.menu, line, z, 0.038, (0.85, 0.93, 1, 1))

        self.make_button(self.menu, "BACK", -0.91, self.show_menu)
        if fetch:
            self.fetch_online_leaderboard()

    def fetch_online_leaderboard(self):
        threading.Thread(target=self._leaderboard_worker, daemon=True).start()

    def _leaderboard_worker(self):
        try:
            request = urllib.request.Request(
                self.api_base_url + "/leaderboard",
                headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.net_in.put({"type": "leaderboard_result", "entries": data.get("entries", [])})
        except Exception as error:
            self.net_in.put({"type": "leaderboard_result", "error": str(error)[:150], "entries": []})

    def show_account(self):
        self.state = "account"
        self.clear_menu()
        self.hide_game()
        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.98),
            frameSize=(-1.5, 1.5, -1, 1)
        )
        self.make_label(self.menu, "NEONARENA ACCOUNT", 0.78, 0.11, (0.1, 0.85, 1, 1))

        if self.account_token:
            self.make_label(self.menu, f"SIGNED IN AS: {self.account_username}", 0.42, 0.06)
            self.make_label(self.menu, f"PERSONAL BEST: {self.account_best_score}", 0.18, 0.05, (1, 0.8, 0.2, 1))
            self.make_label(self.menu, f"GLOBAL RANK: #{self.account_rank or '-'}", -0.04, 0.05, (0.2, 1, 0.6, 1))
            self.make_button(self.menu, "LOG OUT", -0.40, self.logout_account)
        else:
            self.make_label(self.menu, "USERNAME (3-16 CHARACTERS)", 0.48, 0.04)
            self.username_entry = DirectEntry(
                parent=self.menu, initialText=self.player_name if self.player_name != "PLAYER" else "",
                numLines=1, width=18, scale=0.05, pos=(-0.45, 0, 0.28), focus=0,
                text_fg=(1, 1, 1, 1), frameColor=(0.05, 0.12, 0.22, 1)
            )
            self.make_label(self.menu, "PASSWORD (MINIMUM 8 CHARACTERS)", 0.08, 0.04)
            self.password_entry = DirectEntry(
                parent=self.menu, initialText="", numLines=1, width=18, scale=0.05,
                pos=(-0.45, 0, -0.12), focus=0, obscured=1,
                text_fg=(1, 1, 1, 1), frameColor=(0.05, 0.12, 0.22, 1)
            )
            self.make_button(self.menu, "CREATE ACCOUNT", -0.43, lambda: self.start_auth_request("register"))
            self.make_button(self.menu, "LOG IN", -0.67, lambda: self.start_auth_request("login"))
            self.make_label(self.menu, "Your password is sent securely over HTTPS and stored as a hash.", -0.82, 0.025, (0.65, 0.8, 1, 1))

        self.make_button(self.menu, "BACK", -0.94, self.show_menu)

    def start_auth_request(self, action):
        if self.auth_busy:
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        if not username or not password:
            self.show_message("ENTER A USERNAME AND PASSWORD")
            return
        self.auth_busy = True
        self.make_label(self.menu, "CONNECTING...", -0.86, 0.035, (1, 0.8, 0.2, 1))
        threading.Thread(target=self._auth_worker, args=(action, username, password), daemon=True).start()

    def _auth_worker(self, action, username, password):
        try:
            payload = json.dumps({"username": username, "password": password}).encode("utf-8")
            request = urllib.request.Request(
                self.api_base_url + "/auth/" + action,
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.net_in.put({"type": "auth_result", "data": data})
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8")).get("detail", "Login failed")
            except Exception:
                detail = "Login failed"
            self.net_in.put({"type": "auth_error", "message": str(detail)[:150]})
        except Exception as error:
            self.net_in.put({"type": "auth_error", "message": str(error)[:150]})

    def logout_account(self):
        self.account_token = ""
        self.account_username = ""
        self.account_best_score = 0
        self.account_best_level = 1
        self.account_rank = 0
        self.network_stop()
        self.show_account()

    def save_name_and_refresh(self):
        self.set_player_name()
        self.show_leaderboard()

    # STATISTICS

    def show_statistics(self):
        self.state = "statistics"
        self.clear_menu()
        self.hide_game()

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.98),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        minutes = int(self.total_playtime // 60)
        seconds = int(self.total_playtime % 60)

        stats = [
            ("STATISTICS", 0.78, 0.12, (0.1, 0.85, 1, 1)),
            (f"PLAYER: {self.player_name}", 0.50, 0.05, (1, 1, 1, 1)),
            (f"TOTAL GAMES: {self.games_played}", 0.30, 0.05, (0.9, 0.95, 1, 1)),
            (f"TOTAL PLAYTIME: {minutes}m {seconds}s", 0.12, 0.05, (0.9, 0.95, 1, 1)),
            (f"TOTAL ORBS COLLECTED: {self.total_orbs}", -0.06, 0.05, (0.1, 0.9, 1, 1)),
            (f"TOTAL EP EARNED: {self.total_energy_earned}", -0.24, 0.05, (1, 0.8, 0.2, 1)),
            (f"BEST COMBO: {self.highest_combo}", -0.42, 0.05, (0.7, 0.5, 1, 1)),
            (f"HIGHEST LEVEL: {self.highest_level}/25", -0.60, 0.05, (0.2, 1, 0.5, 1))
        ]

        for text, z, scale, color in stats:
            self.make_label(self.menu, text, z, scale, color)

        self.make_button(
            self.menu, "BACK", -0.88, self.show_menu
        )

    # SHOP

    def get_shop_items(self):
        return [
            {
                "id": "magnet",
                "icon": "[MAG]",
                "name": "ENERGY MAGNET",
                "description": "Collect orbs from farther away",
                "price": 100,
                "level": 3
            },
            {
                "id": "double_energy",
                "icon": "[2X]",
                "name": "DOUBLE ENERGY",
                "description": "Earn double EnergyPoints",
                "price": 175,
                "level": 5
            },
            {
                "id": "extra_shield",
                "icon": "[SHD]",
                "name": "EXTRA SHIELD",
                "description": "Start every level with a shield",
                "price": 250,
                "level": 8
            },
            {
                "id": "speed_plus",
                "icon": "[SPD]",
                "name": "SPEED PLUS",
                "description": "Move faster permanently",
                "price": 300,
                "level": 12
            },
            {
                "id": "combo_master",
                "icon": "[COM]",
                "name": "COMBO MASTER",
                "description": "Earn extra EP from combos",
                "price": 400,
                "level": 16
            },
            {
                "id": "mega_magnet",
                "icon": "[MEGA]",
                "name": "MEGA MAGNET",
                "description": "Collect orbs from a huge distance",
                "price": 600,
                "level": 20
            },
            {
                "id": "lucky_orb",
                "icon": "[LUCK]",
                "name": "LUCKY ORB",
                "description": "Higher chance of golden orbs",
                "price": 350,
                "level": 10
            },
            {
                "id": "golden_boost",
                "icon": "[GOLD]",
                "name": "GOLDEN BOOST",
                "description": "Golden orbs give extra rewards",
                "price": 550,
                "level": 18
            },
            {
                "id": "combo_plus",
                "icon": "[PLUS]",
                "name": "COMBO PLUS",
                "description": "Higher maximum combo bonus",
                "price": 450,
                "level": 14
            },
            {
                "id": "second_chance",
                "icon": "[REV]",
                "name": "SECOND CHANCE",
                "description": "Survive one extra bot collision",
                "price": 800,
                "level": 22
            }
        ]

    def show_shop(self):
        self.state = "shop"
        self.clear_menu()
        self.hide_game()

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.98),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        self.make_label(
            self.menu, "ENERGY SHOP",
            0.91, 0.11, (0.1, 0.85, 1, 1)
        )

        self.make_label(
            self.menu,
            f"YOUR ENERGYPOINTS: {self.energy_points}",
            0.75, 0.045, (1, 0.8, 0.2, 1)
        )

        for index, item in enumerate(self.get_shop_items()):
            z = 0.57 - index * 0.145

            if item["id"] in self.owned_abilities:
                status = "OWNED"
                color = (0.2, 1, 0.5, 1)
            elif self.highest_level < item["level"]:
                status = f"LEVEL {item['level']} REQUIRED"
                color = (1, 0.55, 0.25, 1)
            else:
                status = f"{item['price']} EP"
                color = (0.85, 0.95, 1, 1)

            self.make_label(
                self.menu,
                f"{item['icon']}  {item['name']}  |  {status}",
                z, 0.035, color
            )

            self.make_label(
                self.menu,
                item["description"],
                z - 0.055, 0.025,
                (0.7, 0.82, 1, 1)
            )

            if (
                item["id"] not in self.owned_abilities
                and self.highest_level >= item["level"]
            ):
                DirectButton(
                    parent=self.menu,
                    text="BUY",
                    scale=0.032,
                    pos=(1.0, 0, z - 0.025),
                    command=lambda ability=item["id"]:
                        self.buy_ability(ability),
                    frameColor=(0.03, 0.35, 0.5, 1),
                    text_fg=(1, 1, 1, 1)
                )

        self.make_button(
            self.menu, "BACK", -0.91, self.show_menu
        )

    def buy_ability(self, ability_id):
        for item in self.get_shop_items():
            if item["id"] == ability_id:
                if ability_id in self.owned_abilities:
                    return

                if self.highest_level < item["level"]:
                    return

                if self.energy_points < item["price"]:
                    self.show_message("NOT ENOUGH ENERGYPOINTS!")
                    return

                self.energy_points -= item["price"]
                self.owned_abilities.append(ability_id)

                self.save_game()
                self.show_shop()
                return

    # AUDIO

    def generate_audio_files(self):
        sample_rate = 22050
        music_path = os.path.abspath("neon_music.wav")

        if not os.path.exists(music_path):
            with wave.open(music_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)

                samples = []

                for i in range(sample_rate * 4):
                    t = i / sample_rate
                    value = (
                        math.sin(2 * math.pi * 110 * t) * 0.12
                        + math.sin(2 * math.pi * 165 * t) * 0.08
                        + math.sin(2 * math.pi * 220 * t) * 0.05
                    )
                    samples.append(
                        struct.pack("<h", int(value * 32767))
                    )

                wav.writeframes(b"".join(samples))

        effect_path = os.path.abspath("neon_pickup.wav")

        if not os.path.exists(effect_path):
            with wave.open(effect_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)

                samples = []

                for i in range(int(sample_rate * 0.2)):
                    t = i / sample_rate
                    frequency = 500 + 900 * t
                    value = math.sin(2 * math.pi * frequency * t)
                    fade = max(0, 1 - t / 0.2)
                    samples.append(
                        struct.pack("<h", int(value * fade * 10000))
                    )

                wav.writeframes(b"".join(samples))

    def create_audio(self):
        try:
            self.generate_audio_files()

            self.music = self.loader.loadMusic(
                os.path.abspath("neon_music.wav")
            )

            self.pickup_sound = self.loader.loadSfx(
                os.path.abspath("neon_pickup.wav")
            )

            if self.music:
                self.music.setLoop(True)
                self.music.setVolume(self.music_volume)
                self.music.play()

        except Exception as error:
            print("Audio error:", error)

    def play_pickup(self):
        if self.pickup_sound:
            self.pickup_sound.setVolume(self.effects_volume)
            self.pickup_sound.play()

    # LIGHTING

    def setup_lighting(self):
        ambient = AmbientLight("ambient")
        ambient.setColor(Vec4(0.35, 0.35, 0.55, 1))
        self.render.setLight(self.render.attachNewNode(ambient))

        light = DirectionalLight("main-light")
        light.setColor(Vec4(0.8, 0.85, 1, 1))
        node = self.render.attachNewNode(light)
        node.setHpr(-35, -55, 0)
        self.render.setLight(node)

    # LEVEL SETTINGS

    def get_level_settings(self, level):
        colors = [
            (0.05, 0.8, 1, 1),
            (0.8, 0.2, 1, 1),
            (1, 0.35, 0.1, 1),
            (0.1, 1, 0.55, 1),
            (1, 0.2, 0.5, 1),
            (1, 0.8, 0.1, 1),
            (0.3, 0.5, 1, 1),
            (0.7, 1, 0.2, 1),
            (1, 0.4, 0.7, 1),
            (0.2, 1, 1, 1)
        ]

        names = [
            "NEON CITY", "PURPLE ZONE", "FIRE ARENA",
            "CYBER GARDEN", "PINK DISTRICT", "GOLDEN CORE",
            "BLUE STORM", "TOXIC WORLD", "LASER CITY",
            "NEON NIGHT", "DARK MATRIX", "CRYSTAL ARENA",
            "VOLTAGE ZONE", "NOVA CITY", "SHADOW WORLD",
            "QUANTUM CORE", "TURBO ARENA", "INFINITY ZONE",
            "PLASMA WORLD", "CYBER STORM", "ULTRA CITY",
            "CHAOS ARENA", "OMEGA ZONE", "FINAL FRONTIER",
            "THE LAST ARENA"
        ]

        return {
            "color": colors[(level - 1) % len(colors)],
            "orbs": 12 + (level - 1) * 2,
            "bots": min(3 + (level - 1) // 2, 15),
            "name": names[level - 1],
            "bot_speed": 1.5 + (level - 1) * 0.18,
            "bot_range": min(3 + (level - 1) * 0.12, 7),
            "obstacles": min(4 + level // 2, 16)
        }

    def create_level(self, level):
        self.level = max(1, min(self.max_level, level))
        self.highest_level = max(self.highest_level, self.level)
        self.save_game()

        self.score = 0
        self.collected_orbs = 0
        self.speed_boost = 0
        self.shield = "extra_shield" in self.owned_abilities
        self.combo = 0
        self.last_pickup = -100
        self.second_chance_used = False

        if hasattr(self, "arena"):
            self.arena.removeNode()

        # FIX: orbs are dictionaries; remove their model instead.
        for orb_data in self.orbs:
            orb_data["model"].removeNode()

        for bot in self.bots:
            bot["model"].removeNode()

        for powerup in self.powerups:
            powerup["model"].removeNode()

        self.orbs = []
        self.bots = []
        self.powerups = []

        settings = self.get_level_settings(self.level)

        self.level_orbs = settings["orbs"]
        self.level_color = settings["color"]
        self.level_name = settings["name"]
        self.bot_speed = settings["bot_speed"]
        self.bot_range = settings["bot_range"]
        self.obstacle_count = settings["obstacles"]

        self.create_arena()
        self.create_collectibles()
        self.create_bots()
        self.create_powerups()

        self.arena.hide()

        # FIX: hide the model inside each orb dictionary.
        for orb_data in self.orbs:
            orb_data["model"].hide()

        for bot in self.bots:
            bot["model"].hide()

        for powerup in self.powerups:
            powerup["model"].hide()

        self.update_hud()

    # 3D OBJECTS

    def make_box(self, parent, scale, pos, color):
        model = self.loader.loadModel("models/box")
        model.reparentTo(parent)
        model.setScale(*scale)
        model.setPos(*pos)
        model.setColor(*color)
        return model

    def create_arena(self):
        self.arena = self.render.attachNewNode("arena")

        self.make_box(
            self.arena, (20, 20, 0.3), (0, 0, -0.5),
            (0.035, 0.045, 0.1, 1)
        )

        for x in [-20, 20]:
            self.make_box(
                self.arena, (0.3, 20, 3), (x, 0, 2.5),
                self.level_color
            )

        for y in [-20, 20]:
            self.make_box(
                self.arena, (20, 0.3, 3), (0, y, 2.5),
                self.level_color
            )

        for i in range(-16, 17, 4):
            self.make_box(
                self.arena, (0.025, 19, 0.025), (i, 0, -0.15),
                self.level_color
            )

            self.make_box(
                self.arena, (19, 0.025, 0.025), (0, i, -0.15),
                self.level_color
            )

        positions = [
            (-8, -3), (8, -3), (-8, 8), (8, 8),
            (0, 8), (0, -8), (-13, 0), (13, 0),
            (-5, 12), (5, -12), (-13, -12), (13, 12),
            (-5, -5), (5, 5), (-13, 8), (13, -8)
        ]

        for i in range(self.obstacle_count):
            x, y = positions[i]

            self.make_box(
                self.arena, (0.8, 0.8, 2), (x, y, 1.5),
                (0.08, 0.12, 0.25, 1)
            )

            self.make_box(
                self.arena, (0.95, 0.95, 0.08), (x, y, 3.55),
                self.level_color
            )

    # COLLECTIBLES

    def create_collectibles(self):
        for i in range(self.level_orbs):
            x = random.uniform(-15, 15)
            y = random.uniform(-15, 15)

            orb = self.loader.loadModel("models/misc/sphere")
            orb.reparentTo(self.render)
            orb.setScale(0.45)
            orb.setPos(x, y, 1.1)

            is_golden = random.random() < (
                0.20 if "lucky_orb" in self.owned_abilities else 0.08
            )

            if is_golden:
                orb.setColor(1, 0.75, 0.05, 1)
            else:
                orb.setColor(*self.level_color)

            orb.setLightOff()
            orb.hide()

            self.orbs.append({
                "model": orb,
                "golden": is_golden
            })

    # POWERUPS

    def create_powerups(self):
        choices = [
            ("shield", (0.1, 1, 0.3, 1)),
            ("speed", (1, 0.8, 0.05, 1))
        ]

        for kind, color in choices:
            model = self.loader.loadModel("models/misc/sphere")
            model.reparentTo(self.render)
            model.setScale(0.7)
            model.setPos(
                random.uniform(-13, 13),
                random.uniform(-13, 13),
                1.1
            )
            model.setColor(*color)
            model.setLightOff()
            model.hide()

            self.powerups.append({
                "model": model,
                "kind": kind
            })

    def update_powerups(self, time):
        for powerup in self.powerups:
            model = powerup["model"]
            if not model.isHidden():
                model.setH(time * 90)

    def check_powerups(self):
        player = self.camera.getPos()

        for powerup in self.powerups:
            model = powerup["model"]

            if model.isHidden():
                continue

            if (model.getPos() - player).length() < 1.7:
                kind = powerup["kind"]
                model.hide()
                self.play_pickup()

                if kind == "shield":
                    self.shield = True
                    self.show_message("SHIELD ACTIVATED!")

                elif kind == "speed":
                    self.speed_boost = 8
                    self.show_message("SPEED BOOST!")

                self.update_hud()

    # BOTS

    def create_bots(self):
        bot_count = self.get_level_settings(self.level)["bots"]

        for i in range(bot_count):
            bot = self.loader.loadModel("models/box")
            bot.reparentTo(self.render)
            bot.setScale(0.8, 0.8, 0.8)

            start_x = i * 5 - (bot_count - 1) * 2.5
            bot.setPos(start_x, 5, 0.5)
            bot.setColor(0.9, 0.15, 0.65, 1)
            bot.hide()

            self.bots.append({
                "model": bot,
                "start_x": start_x,
                "phase": i * 1.5
            })

    # HUD

    def create_hud(self):
        self.hud = DirectLabel(
            text="",
            scale=0.06,
            pos=(-1.15, 0, 0.9),
            text_fg=(0.2, 0.9, 1, 1),
            frameColor=(0, 0, 0, 0),
            text_align=0
        )
        self.hud.hide()

        self.message = DirectLabel(
            text="",
            scale=0.08,
            pos=(0, 0, 0.72),
            text_fg=(1, 1, 1, 1),
            frameColor=(0, 0, 0, 0)
        )
        self.message.hide()

        self.multiplayer_label = DirectLabel(
            text="MULTIPLAYER: OFFLINE",
            scale=0.043,
            pos=(1.12, 0, 0.91),
            text_fg=(0.8, 0.85, 1, 1),
            frameColor=(0, 0, 0, 0),
            text_align=2
        )
        self.multiplayer_label.hide()

    def update_hud(self):
        self.hud["text"] = (
            f"LEVEL {self.level}/25 - {self.level_name}\n"
            f"ENERGY: {self.collected_orbs}/{self.level_orbs}\n"
            f"SCORE: {self.score} | TOTAL: {self.run_score}\n"
            f"ENERGYPOINTS: {self.energy_points}\n"
            f"COMBO: {self.combo}\n"
            f"SHIELD: {'READY' if self.shield else 'NONE'}"
        )

    def show_message(self, text):
        self.message["text"] = text
        self.message.show()

    # MENUS

    def clear_menu(self):
        if hasattr(self, "menu"):
            self.menu.destroy()

    def make_button(self, parent, text, z, command):
        return DirectButton(
            parent=parent,
            text=text,
            scale=0.065,
            pos=(0, 0, z),
            command=command,
            frameColor=(0.03, 0.25, 0.45, 1),
            text_fg=(1, 1, 1, 1),
            relief=1
        )

    def make_label(
        self, parent, text, z, scale=0.05,
        color=(0.85, 0.93, 1, 1)
    ):
        return DirectLabel(
            parent=parent,
            text=text,
            scale=scale,
            pos=(0, 0, z),
            text_fg=color,
            frameColor=(0, 0, 0, 0)
        )

    def show_menu(self):
        self.state = "menu"
        self.clear_menu()
        self.hide_game()

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.97),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        self.make_label(
            self.menu, "NEONARENA",
            0.85, 0.14, (0.1, 0.85, 1, 1)
        )

        self.make_label(
            self.menu,
            f"ENERGYPOINTS: {self.energy_points}",
            0.65, 0.045, (1, 0.8, 0.2, 1)
        )

        daily_text = (
            "DAILY REWARD: CLAIM +100 EP"
            if self.can_claim_daily()
            else "DAILY REWARD: ALREADY CLAIMED"
        )

        self.make_button(
            self.menu, daily_text, 0.43,
            self.claim_daily_reward
        )

        self.make_button(
            self.menu, "PLAY", 0.19, self.start_new_game
        )

        self.make_button(
            self.menu, "ENERGY SHOP", -0.03, self.show_shop
        )

        self.make_button(
            self.menu, "LEADERBOARD", -0.25,
            self.show_leaderboard
        )

        self.make_button(
            self.menu, "STATISTICS", -0.47,
            self.show_statistics
        )

        self.make_button(
            self.menu, "SETTINGS", -0.69,
            self.open_settings
        )

        DirectButton(
            parent=self.menu,
            text=(f"ACCOUNT: {self.account_username}" if self.account_token else "ACCOUNT / LOGIN"),
            scale=0.043,
            pos=(0.98, 0, 0.82),
            command=self.show_account,
            frameColor=(0.03, 0.35, 0.5, 1),
            text_fg=(1, 1, 1, 1)
        )

        self.make_button(
            self.menu, "QUIT", -0.90, self.userExit
        )

    # SETTINGS

    def open_settings(self):
        self.settings_return_state = self.state
        self.show_settings()

    def show_settings(self):
        self.clear_menu()

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.97),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        self.make_label(
            self.menu, "SETTINGS",
            0.72, 0.12, (0.1, 0.85, 1, 1)
        )

        self.make_label(
            self.menu, "MOUSE SENSITIVITY",
            0.46, 0.05, (0.9, 0.95, 1, 1)
        )

        DirectSlider(
            parent=self.menu,
            range=(0.05, 0.5),
            value=self.sensitivity,
            pageSize=0.01,
            scale=0.4,
            pos=(0, 0, 0.27),
            command=self.change_sensitivity
        )

        self.make_label(
            self.menu, "MUSIC VOLUME",
            0.08, 0.05, (0.9, 0.95, 1, 1)
        )

        DirectSlider(
            parent=self.menu,
            range=(0, 1),
            value=self.music_volume,
            pageSize=0.05,
            scale=0.4,
            pos=(0, 0, -0.11),
            command=self.change_music_volume
        )

        self.make_label(
            self.menu, "SOUND EFFECTS",
            -0.29, 0.05, (0.9, 0.95, 1, 1)
        )

        DirectSlider(
            parent=self.menu,
            range=(0, 1),
            value=self.effects_volume,
            pageSize=0.05,
            scale=0.4,
            pos=(0, 0, -0.48),
            command=self.change_effects_volume
        )

        self.make_button(
            self.menu, "BACK", -0.78,
            self.back_from_settings
        )

    def change_sensitivity(self, value=None):
        if value is not None:
            self.sensitivity = float(value)

    def change_music_volume(self, value=None):
        if value is not None:
            self.music_volume = float(value)

        if self.music:
            self.music.setVolume(self.music_volume)

    def change_effects_volume(self, value=None):
        if value is not None:
            self.effects_volume = float(value)

    def back_from_settings(self):
        if self.settings_return_state == "paused":
            self.show_pause()
        else:
            self.show_menu()

    # GAME

    def start_new_game(self):
        self.run_score = 0
        self.run_recorded = False
        self.games_played += 1
        self.save_game()
        self.create_level(1)
        self.start_game()

    def start_game(self):
        self.clear_menu()
        self.state = "playing"

        self.speed_boost = 0
        self.shield = "extra_shield" in self.owned_abilities
        self.combo = 0
        self.last_pickup = -100

        self.message.hide()
        self.arena.show()
        self.hud.show()
        self.multiplayer_label.show()
        self.network_start()

        for orb_data in self.orbs:
            orb_data["model"].show()

        for bot in self.bots:
            bot["model"].show()

        for powerup in self.powerups:
            powerup["model"].show()

        self.camera.setPos(0, -14, 1.7)
        self.camera.setHpr(0, 0, 0)

        self.update_hud()
        self.capture_mouse()

    def hide_game(self):
        if hasattr(self, "arena"):
            self.arena.hide()

        self.network_stop()
        self.hud.hide()
        self.message.hide()
        self.multiplayer_label.hide()

        for orb_data in self.orbs:
            orb_data["model"].hide()

        for bot in self.bots:
            bot["model"].hide()

        for powerup in self.powerups:
            powerup["model"].hide()

        self.release_mouse()

    # MOUSE

    def capture_mouse(self):
        if self.state != "playing":
            return

        self.mouse_active = True

        props = WindowProperties()
        props.setCursorHidden(True)
        self.win.requestProperties(props)

        self.mouse_initialized = False

    def release_mouse(self):
        self.mouse_active = False

        props = WindowProperties()
        props.setCursorHidden(False)
        self.win.requestProperties(props)

    def update_mouse(self):
        if not self.mouse_active:
            return

        pointer = self.win.getPointer(0)
        x = pointer.getX()
        y = pointer.getY()

        if not self.mouse_initialized:
            self.win.movePointer(0, self.center_x, self.center_y)
            self.mouse_initialized = True
            return

        dx = x - self.center_x
        dy = y - self.center_y

        self.camera.setH(
            self.camera.getH() - dx * self.sensitivity
        )

        self.camera.setP(
            max(-80, min(
                80,
                self.camera.getP() - dy * self.sensitivity
            ))
        )

        self.win.movePointer(
            0, self.center_x, self.center_y
        )

    # KEYBOARD

    def set_key(self, key, value):
        self.keys[key] = value

    def update_player(self, dt):
        speed = 8

        if "speed_plus" in self.owned_abilities:
            speed = 10

        if self.speed_boost > 0:
            speed += 6

        speed *= dt

        heading = math.radians(self.camera.getH())

        forward = Vec3(
            -math.sin(heading),
            math.cos(heading),
            0
        )

        right = Vec3(
            math.cos(heading),
            math.sin(heading),
            0
        )

        movement = Vec3(0, 0, 0)

        if self.keys.get("z") or self.keys.get("w"):
            movement += forward

        if self.keys.get("s"):
            movement -= forward

        if self.keys.get("d"):
            movement += right

        if self.keys.get("q") or self.keys.get("a"):
            movement -= right

        if movement.length() > 0:
            movement.normalize()
            self.camera.setPos(
                self.camera.getPos() + movement * speed
            )

        pos = self.camera.getPos()
        self.camera.setX(max(-18, min(18, pos.x)))
        self.camera.setY(max(-18, min(18, pos.y)))
        self.camera.setZ(1.7)

    # BOTS

    def update_bots(self, time):
        for bot in self.bots:
            model = bot["model"]

            x = bot["start_x"] + math.sin(
                time * self.bot_speed + bot["phase"]
            ) * self.bot_range

            model.setX(x)

            model.setY(
                5 + math.sin(
                    time * (1 + self.level * 0.025)
                    + bot["phase"]
                ) * self.bot_range * 0.65
            )

            model.setH(time * (40 + self.level * 4))

    # COLLECTING

    def check_collectibles(self, time):
        player = self.camera.getPos()
        magnet_range = 1.5

        if "magnet" in self.owned_abilities:
            magnet_range = 3.2

        if "mega_magnet" in self.owned_abilities:
            magnet_range = 5.0

        for orb_data in self.orbs:
            orb = orb_data["model"]

            if orb.isHidden():
                continue

            orb.setH(time * 80)

            if (orb.getPos() - player).length() < magnet_range:
                orb.hide()
                self.collected_orbs += 1
                self.total_orbs += 1

                if time - self.last_pickup < 4:
                    self.combo += 1
                else:
                    self.combo = 1

                self.last_pickup = time
                self.highest_combo = max(
                    self.highest_combo, self.combo
                )

                bonus_limit = (
                    10 if "combo_plus" in self.owned_abilities else 5
                )

                bonus = min(self.combo - 1, bonus_limit)
                self.score += 1 + bonus
                self.run_score += 1 + bonus

                energy_reward = 2

                if "double_energy" in self.owned_abilities:
                    energy_reward *= 2

                if "combo_master" in self.owned_abilities:
                    energy_reward += min(self.combo, 5)

                if orb_data["golden"]:
                    golden_bonus = 10

                    if "golden_boost" in self.owned_abilities:
                        golden_bonus = 25

                    energy_reward += golden_bonus
                    self.run_score += 5
                    self.show_message(
                        f"GOLDEN ORB! +{energy_reward} EP!"
                    )

                self.add_energy_points(energy_reward)
                self.play_pickup()
                self.update_hud()

                if self.collected_orbs >= self.level_orbs:
                    self.level_complete()
                    return

        if self.collected_orbs >= self.level_orbs:
            self.level_complete()

    # COLLISION

    def check_bots(self):
        player = self.camera.getPos()

        for bot in self.bots:
            model = bot["model"]

            if (model.getPos() - player).length() < 1.5:

                if self.shield:
                    self.shield = False
                    self.camera.setPos(0, -14, 1.7)
                    self.camera.setHpr(0, 0, 0)
                    self.show_message("SHIELD USED!")
                    self.update_hud()
                    return

                if (
                    "second_chance" in self.owned_abilities
                    and not self.second_chance_used
                ):
                    self.second_chance_used = True
                    self.camera.setPos(0, -14, 1.7)
                    self.camera.setHpr(0, 0, 0)
                    self.show_message("SECOND CHANCE USED!")
                    return

                self.game_over()
                return

    # GAME OVER

    def game_over(self):
        if self.state != "playing":
            return

        self.state = "game_over"
        self.release_mouse()
        self.network_stop()
        self.record_result()
        self.clear_menu()

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.97),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        self.make_label(
            self.menu, "GAME OVER",
            0.5, 0.16, (1, 0.2, 0.3, 1)
        )

        self.make_label(
            self.menu, f"YOU REACHED LEVEL {self.level}",
            0.2, 0.055, (0.85, 0.93, 1, 1)
        )

        self.make_label(
            self.menu, f"TOTAL SCORE: {self.run_score}",
            0, 0.05, (1, 0.8, 0.2, 1)
        )

        self.make_label(
            self.menu, f"ENERGYPOINTS: {self.energy_points}",
            -0.16, 0.045, (0.1, 0.9, 1, 1)
        )

        self.make_button(
            self.menu, "TRY AGAIN",
            -0.43, self.restart_game
        )

        self.make_button(
            self.menu, "MAIN MENU",
            -0.70, self.show_menu
        )

    def restart_game(self):
        self.run_score = 0
        self.run_recorded = False
        self.create_level(1)
        self.start_game()

    # LEVEL COMPLETE

    def level_complete(self):
        if self.state != "playing":
            return

        self.state = "level_complete"
        self.release_mouse()
        self.clear_menu()

        level_bonus = self.level * 25
        self.add_energy_points(level_bonus)

        if self.level == self.max_level:
            self.record_result()
            title = "YOU BEAT ALL 25 LEVELS!"
            button_text = "PLAY AGAIN"
            command = self.restart_game
        else:
            title = "LEVEL COMPLETED!"
            button_text = "NEXT LEVEL"
            command = self.next_level

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.97),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        self.make_label(
            self.menu, title,
            0.45, 0.11, (0.1, 1, 0.6, 1)
        )

        self.make_label(
            self.menu,
            f"LEVEL {self.level}/25 - {self.level_name}",
            0.15, 0.05, (0.9, 0.95, 1, 1)
        )

        self.make_label(
            self.menu,
            f"LEVEL BONUS: +{level_bonus} ENERGYPOINTS",
            -0.05, 0.045, (1, 0.8, 0.2, 1)
        )

        self.make_label(
            self.menu, f"TOTAL SCORE: {self.run_score}",
            -0.22, 0.045, (0.85, 0.93, 1, 1)
        )

        self.make_button(
            self.menu, button_text,
            -0.48, command
        )

        self.make_button(
            self.menu, "MAIN MENU",
            -0.73, self.show_menu
        )

    def next_level(self):
        if self.level < self.max_level:
            self.create_level(self.level + 1)
            self.start_game()

    # PAUSE

    def escape(self):
        if self.state == "playing":
            self.show_pause()
        elif self.state == "paused":
            self.resume_game()

    def show_pause(self):
        self.state = "paused"
        self.release_mouse()
        self.clear_menu()

        self.menu = DirectFrame(
            frameColor=(0.01, 0.02, 0.08, 0.95),
            frameSize=(-1.5, 1.5, -1, 1)
        )

        self.make_label(
            self.menu, "PAUSED",
            0.5, 0.14, (0.1, 0.85, 1, 1)
        )

        self.make_button(
            self.menu, "RESUME",
            0.1, self.resume_game
        )

        self.make_button(
            self.menu, "SETTINGS",
            -0.2, self.open_settings
        )

        self.make_button(
            self.menu, "MAIN MENU",
            -0.5, self.show_menu
        )

    def resume_game(self):
        self.clear_menu()
        self.state = "playing"
        self.capture_mouse()

    # ONLINE MULTIPLAYER

    def network_start(self):
        """Start the authenticated WebSocket worker without blocking the game loop."""
        if not self.account_token:
            self.multiplayer_status = "LOGIN REQUIRED"
            self.update_multiplayer_label()
            return
        if self.net_thread is not None and self.net_thread.is_alive():
            self.net_running = True
            return

        # Remove any stale shutdown markers or old movement packets.
        while True:
            try:
                self.net_out.get_nowait()
            except queue.Empty:
                break

        self.net_running = True
        self.multiplayer_status = "CONNECTING..."
        self.update_multiplayer_label()
        self.net_thread = threading.Thread(
            target=self._network_thread_main,
            name="NeonArena-WebSocket",
            daemon=True
        )
        self.net_thread.start()

    def network_stop(self):
        """Close the room connection when the player leaves the game."""
        self.net_running = False
        try:
            self.net_out.put_nowait(None)
        except Exception:
            pass

        if self.net_thread is not None and self.net_thread.is_alive():
            self.net_thread.join(timeout=0.35)

        self.net_connected = False
        self.net_player_id = None
        self.net_room = ""
        self.multiplayer_status = "OFFLINE"

        for player_id in list(self.remote_players.keys()):
            self.remove_remote_player(player_id)

        self.update_multiplayer_label()

    def _network_thread_main(self):
        try:
            asyncio.run(self._network_worker())
        except Exception as error:
            self.net_in.put({
                "type": "network_status",
                "status": "ERROR",
                "detail": str(error)[:120]
            })

    async def _network_worker(self):
        try:
            import websockets
        except ImportError:
            self.net_in.put({
                "type": "network_status",
                "status": "MISSING WEBSOCKETS",
                "detail": "Install the websockets package"
            })
            return

        while self.net_running:
            try:
                async with websockets.connect(
                    self.server_url,
                    open_timeout=12,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3
                ) as websocket:
                    await websocket.send(json.dumps({
                        "type": "join",
                        "token": self.account_token
                    }))
                    self.net_in.put({
                        "type": "network_status",
                        "status": "CONNECTED",
                        "detail": "Connected to matchmaking"
                    })

                    async def sender():
                        while self.net_running:
                            try:
                                packet = await asyncio.to_thread(
                                    self.net_out.get, True, 0.5
                                )
                            except queue.Empty:
                                continue
                            if packet is None:
                                return
                            await websocket.send(json.dumps(packet))

                    async def receiver():
                        async for raw_message in websocket:
                            try:
                                packet = json.loads(raw_message)
                                if isinstance(packet, dict):
                                    self.net_in.put(packet)
                            except (json.JSONDecodeError, TypeError):
                                continue

                    sender_task = asyncio.create_task(sender())
                    receiver_task = asyncio.create_task(receiver())
                    done, pending = await asyncio.wait(
                        [sender_task, receiver_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

                    for task in done:
                        try:
                            task.result()
                        except Exception:
                            pass

            except Exception as error:
                if self.net_running:
                    self.net_in.put({
                        "type": "network_status",
                        "status": "RECONNECTING...",
                        "detail": str(error)[:100]
                    })
                    await asyncio.sleep(5)

        self.net_in.put({
            "type": "network_status",
            "status": "OFFLINE",
            "detail": "Disconnected"
        })

    def update_multiplayer_label(self):
        if not hasattr(self, "multiplayer_label"):
            return

        if self.net_connected and self.net_room:
            text = f"ONLINE: {self.net_room} | PLAYERS: {len(self.remote_players) + 1}/4"
            color = (0.2, 1, 0.55, 1)
        else:
            text = f"MULTIPLAYER: {self.multiplayer_status}"
            color = (1, 0.75, 0.25, 1) if self.multiplayer_status != "OFFLINE" else (0.8, 0.85, 1, 1)

        self.multiplayer_label["text"] = text
        self.multiplayer_label["text_fg"] = color

    def add_remote_player(self, player_id, name="PLAYER"):
        if not player_id or player_id == self.net_player_id:
            return
        if player_id in self.remote_players:
            self.remote_players[player_id]["name"] = str(name)[:16]
            return

        root = self.render.attachNewNode("remote-player-" + str(player_id))
        body = self.loader.loadModel("models/box")
        body.reparentTo(root)
        body.setScale(0.48, 0.35, 0.85)
        body.setPos(0, 0, 0.9)

        head = self.loader.loadModel("models/misc/sphere")
        head.reparentTo(root)
        head.setScale(0.32)
        head.setPos(0, 0, 1.85)

        color_index = sum(ord(char) for char in str(player_id)) % 4
        colors = [
            (0.1, 0.8, 1, 1),
            (1, 0.35, 0.75, 1),
            (0.35, 1, 0.55, 1),
            (1, 0.75, 0.15, 1)
        ]
        body.setColor(*colors[color_index])
        head.setColor(0.9, 0.95, 1, 1)
        body.setLightOff()
        head.setLightOff()

        self.remote_players[player_id] = {
            "model": root,
            "name": str(name)[:16]
        }
        self.update_multiplayer_label()

    def remove_remote_player(self, player_id):
        player = self.remote_players.pop(player_id, None)
        if player:
            player["model"].removeNode()
        self.update_multiplayer_label()

    def process_network_messages(self):
        # Only the Panda3D main thread changes 3D models and GUI elements.
        for _ in range(100):
            try:
                packet = self.net_in.get_nowait()
            except queue.Empty:
                break

            kind = packet.get("type")

            if kind == "auth_result":
                data = packet.get("data", {})
                self.auth_busy = False
                self.account_token = data.get("token", "")
                self.account_username = data.get("username", "")
                self.player_name = self.account_username[:16]
                self.account_best_score = int(data.get("best_score", 0))
                self.account_best_level = int(data.get("best_level", 1))
                self.account_rank = int(data.get("rank", 1))
                self.save_game()
                self.show_account()

            elif kind == "auth_error":
                self.auth_busy = False
                self.show_account()
                self.make_label(self.menu, packet.get("message", "Login failed"), -0.82, 0.032, (1, 0.3, 0.35, 1))

            elif kind == "leaderboard_result":
                self.online_leaderboard = packet.get("entries", [])
                if self.state == "leaderboard":
                    self.show_leaderboard(fetch=False)

            elif kind == "score_result":
                data = packet.get("data", {})
                self.account_best_score = int(data.get("best_score", self.account_best_score))
                self.account_best_level = int(data.get("best_level", self.account_best_level))
                self.account_rank = int(data.get("rank", self.account_rank or 1))
                if data.get("new_record"):
                    self.show_message("NEW PERSONAL RECORD SAVED ONLINE!")
                if self.state == "leaderboard":
                    self.fetch_online_leaderboard()

            elif kind == "score_error":
                print("Online score could not be saved:", packet.get("message", "Unknown error"))

            elif kind == "network_status":
                status = packet.get("status", "OFFLINE")
                self.multiplayer_status = status
                self.net_connected = status == "CONNECTED"
                if status != "CONNECTED":
                    self.net_player_id = None
                    self.net_room = ""
                self.update_multiplayer_label()

            elif kind == "joined":
                self.net_player_id = packet.get("player_id")
                self.net_room = packet.get("room", "")
                self.net_connected = True
                self.multiplayer_status = "CONNECTED"
                self.update_multiplayer_label()

            elif kind == "players":
                for player in packet.get("players", []):
                    self.add_remote_player(
                        player.get("player_id"),
                        player.get("name", "PLAYER")
                    )

            elif kind == "player_joined":
                self.add_remote_player(
                    packet.get("player_id"),
                    packet.get("name", "PLAYER")
                )

            elif kind == "player_left":
                self.remove_remote_player(packet.get("player_id"))

            elif kind == "error":
                self.multiplayer_status = str(packet.get("message", "SERVER ERROR"))[:28].upper()
                self.update_multiplayer_label()

            elif kind == "state":
                player_id = packet.get("player_id")
                state_data = packet.get("state", {})
                if not isinstance(state_data, dict):
                    continue
                self.add_remote_player(player_id, state_data.get("name", "PLAYER"))
                remote = self.remote_players.get(player_id)
                if remote:
                    try:
                        x = max(-18.0, min(18.0, float(state_data.get("x", 0))))
                        y = max(-18.0, min(18.0, float(state_data.get("y", 0))))
                        z = max(0.0, min(4.0, float(state_data.get("z", 1.7))))
                        heading = float(state_data.get("h", 0))
                        remote["model"].setPos(x, y, max(0.0, z - 1.7))
                        remote["model"].setH(heading)
                    except (TypeError, ValueError):
                        pass

    def send_network_state(self, dt):
        if not self.net_connected or self.state != "playing":
            return

        self.net_send_timer += dt
        if self.net_send_timer < 0.05:
            return
        self.net_send_timer = 0.0

        pos = self.camera.getPos()
        packet = {
            "type": "state",
            "state": {
                "x": float(pos.x),
                "y": float(pos.y),
                "z": float(pos.z),
                "h": float(self.camera.getH()),
                "p": float(self.camera.getP()),
                "name": self.player_name[:16]
            }
        }
        try:
            self.net_out.put_nowait(packet)
        except queue.Full:
            pass

    # UPDATE

    def update(self, task):
        dt = min(globalClock.getDt(), 0.05)
        self.process_network_messages()

        if self.state != "playing":
            return task.cont

        self.total_playtime += dt

        self.update_mouse()
        self.update_player(dt)
        self.send_network_state(dt)
        self.update_bots(task.time)
        self.update_powerups(task.time)
        self.check_collectibles(task.time)
        self.check_powerups()

        if self.speed_boost > 0:
            self.speed_boost = max(0, self.speed_boost - dt)

        self.check_bots()

        # Save playtime only after another 10 seconds have passed.
        if self.total_playtime - self.last_playtime_save >= 10:
            self.last_playtime_save = self.total_playtime
            self.save_game()

        return task.cont


game = NeonArena()
game.run()