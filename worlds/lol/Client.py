from __future__ import annotations
import os
import sys
import asyncio
import shutil
import requests
import json

import ModuleUpdate
ModuleUpdate.update()

import Utils

check_num = 0

###Set up game communication path###
if "localappdata" in os.environ:
    game_communication_path = os.path.expandvars(r"%localappdata%/LOLAP")
else:
    game_communication_path = os.path.expandvars(r"$HOME/LOLAP")
if not os.path.exists(game_communication_path):
    os.makedirs(game_communication_path)


###Client###
if __name__ == "__main__":
    Utils.init_logging("LOLClient", exception_logger="Client")

from NetUtils import NetworkItem, ClientStatus
from CommonClient import gui_enabled, logger, get_base_parser, ClientCommandProcessor, \
    CommonContext, server_loop

tracker_loaded = False
try:
    from worlds.tracker.TrackerClient import TrackerGameContext as SuperContext
    tracker_loaded = True
except ModuleNotFoundError:
    SuperContext = CommonContext


def check_stdin() -> None:
    if Utils.is_windows and sys.stdin:
        print("WARNING: Console input is not routed reliably on Windows, use the GUI instead.")

class LOLClientCommandProcessor(ClientCommandProcessor):
    def _cmd_send_starting_champions(self):
        """Manually trigger the starting champion location checks."""
        ctx: LOLContext = self.ctx
        starting_count = int(ctx.slot_data.get('Starting Champion Count', 0)) if ctx.slot_data else 0
        if starting_count == 0:
            logger.info("No starting champions configured for this slot.")
            return
        for i in range(1, starting_count + 1):
            filepath = os.path.join(ctx.game_communication_path, f"send{566_000000 + i}")
            with open(filepath, 'w') as f:
                pass
        logger.info(f"Triggered {starting_count} starting champion check(s).")

class LOLContext(SuperContext):
    command_processor: int = LOLClientCommandProcessor
    tags = {"AP"}
    game = "League of Legends"
    items_handling = 0b111  # full remote

    def __init__(self, server_address, password):
        super(LOLContext, self).__init__(server_address, password)
        self.send_index: int = 0
        self.syncing = False
        self.awaiting_bridge = False
        self.lp_label = None
        self.required_lp: int = 0
        self.win_completes_champion: bool = False
        self.slot_data: dict = {}
        self._received_items_cache: set = set()  # (item, location, player) already written to disk
        self._last_locations_sent: set = set()  # last set sent in LocationChecks
        self._last_active_location_ids_written: set = set()
        # self.game_communication_path: files go in this path to pass data between us and the actual game
        if "localappdata" in os.environ:
            self.game_communication_path = os.path.expandvars(r"%localappdata%/LOLAP")
        else:
            self.game_communication_path = os.path.expandvars(r"$HOME/LOLAP")
        if not os.path.exists(self.game_communication_path):
            os.makedirs(self.game_communication_path)
        for root, dirs, files in os.walk(self.game_communication_path):
            for file in files:
                if file.find("obtain") <= -1:
                    os.remove(root+"/"+file)

    def _clear_victory_marker(self):
        try:
            os.remove(os.path.join(self.game_communication_path, "victory"))
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _write_hinted_locations(self):
        key = f"_read_hints_{self.team}_{self.slot}"
        hinted_locations = set()
        for hint in self.stored_data.get(key, []):
            if isinstance(hint, dict):
                if hint.get("found", False):
                    continue
                location = hint.get("location")
            else:
                location = getattr(hint, "location", None)
                if getattr(hint, "found", False):
                    continue
            try:
                if location is not None:
                    hinted_locations.add(int(location))
            except (TypeError, ValueError):
                pass
        try:
            with open(os.path.join(self.game_communication_path, "Hinted_Locations.cfg"), 'w') as f:
                f.write(str(sorted(hinted_locations)))
        except Exception:
            pass

    async def server_auth(self, password_requested: bool = False):
        if password_requested and not self.password:
            await super(LOLContext, self).server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    async def connection_closed(self):
        await super(LOLContext, self).connection_closed()
        for root, dirs, files in os.walk(self.game_communication_path):
            for file in files:
                if file.find("obtain") <= -1:
                    os.remove(root + "/" + file)

    @property
    def endpoints(self):
        if self.server:
            return [self.server]
        else:
            return []

    async def shutdown(self):
        await super(LOLContext, self).shutdown()
        for root, dirs, files in os.walk(self.game_communication_path):
            for file in files:
                if file.find("obtain") <= -1:
                    os.remove(root+"/"+file)

    def on_package(self, cmd: str, args: dict):
        slot_changed = False
        if cmd == "Connected":
            previous_identity = (self.team, self.slot)
            new_identity = (args.get("team"), args.get("slot"))
            slot_changed = previous_identity != new_identity and previous_identity != (None, None)
            if slot_changed:
                self.finished_game = False
                self._clear_victory_marker()
        super().on_package(cmd, args)
        if cmd in {"Connected", "Retrieved", "SetReply"}:
            self._write_hinted_locations()
        if cmd in {"Connected"}:
            if not os.path.exists(self.game_communication_path):
                os.makedirs(self.game_communication_path)
            # Offload bulk file creation to a thread so the event loop stays responsive
            checked = list(self.checked_locations)
            comm_path = self.game_communication_path
            def _write_send_files():
                for ss in checked:
                    filename = f"send{ss}"
                    filepath = os.path.join(comm_path, filename)
                    if not os.path.exists(filepath):
                        with open(filepath, 'w') as f:
                            pass
            asyncio.get_event_loop().run_in_executor(None, _write_send_files)
            #Handle Slot Data
            for slot_data_key in list(args['slot_data'].keys()):
                with open(os.path.join(self.game_communication_path, slot_data_key.replace(" ", "_") + ".cfg"), 'w') as f:
                    f.write(str(args['slot_data'][slot_data_key]))
                    f.close()
            #End Handle Slot Data
            self.required_lp = int(args['slot_data'].get('Required LP', 0))
            self.win_completes_champion = bool(args['slot_data'].get('Win Completes Champion', False))
            self.slot_data = args['slot_data']
            # Auto-send starting champion checks so players receive them immediately
            starting_count = int(args['slot_data'].get('Starting Champion Count', 0))
            for i in range(1, starting_count + 1):
                filepath = os.path.join(self.game_communication_path, f"send{566_000000 + i}")
                with open(filepath, 'w') as f:
                    pass
            # win_completes_champion sibling filter uses ctx.missing_locations (set after Connected)
            # Write a checked locations file for tools (list of ids)
            try:
                with open(os.path.join(self.game_communication_path, "Checked_Locations.cfg"), 'w') as f:
                    f.write(str(list(self.checked_locations)))
                    f.close()
            except Exception:
                pass
            
        if cmd in {"ReceivedItems"}:
            start_index = args["index"]
            if start_index != len(self.items_received):
                # Seed the in-memory cache from disk on first use (e.g. after reconnect)
                if not self._received_items_cache:
                    try:
                        for filename in os.listdir(self.game_communication_path):
                            if filename.startswith("AP") and filename.endswith(".item"):
                                with open(os.path.join(self.game_communication_path, filename), 'r') as f:
                                    lines = f.read().splitlines()
                                if len(lines) >= 3:
                                    self._received_items_cache.add((lines[0], lines[1], lines[2]))
                    except Exception:
                        pass
                # Find next file index once
                check_num = 0
                try:
                    for filename in os.listdir(self.game_communication_path):
                        if filename.startswith("AP") and filename.endswith(".item"):
                            try:
                                n = int(filename.split("_")[-1].split(".")[0])
                                if n > check_num:
                                    check_num = n
                            except ValueError:
                                pass
                except Exception:
                    pass
                for item in args['items']:
                    net_item = NetworkItem(*item)
                    key = (str(net_item.item), str(net_item.location), str(net_item.player))
                    if key not in self._received_items_cache and int(net_item.location) > 0:
                        check_num += 1
                        filename = f"AP_{check_num}.item"
                        with open(os.path.join(self.game_communication_path, filename), 'w') as f:
                            f.write(f"{net_item.item}\n{net_item.location}\n{net_item.player}")
                        self._received_items_cache.add(key)

        if cmd in {"RoomUpdate"}:
            if "checked_locations" in args:
                for ss in self.checked_locations:
                    filename = f"send{ss}"
                    with open(os.path.join(self.game_communication_path, filename), 'w') as f:
                        f.close()
            # update checked locations file
            try:
                with open(os.path.join(self.game_communication_path, "Checked_Locations.cfg"), 'w') as f:
                    f.write(str(list(self.checked_locations)))
                    f.close()
            except Exception:
                pass

    async def draw_lp_counter(self):
        try:
            from kvui import MDLabel as Label
        except ImportError:
            from kvui import Label
        # Only show LP counter when we've got a slot and a non-zero required LP
        if not getattr(self, 'slot', None) or not self.required_lp:
            return
        if not self.lp_label:
            # make the label smaller so it doesn't take too much space
            self.lp_label = Label(text="", size_hint_x=None, width=84, halign="center")
            self.ui.connect_layout.add_widget(self.lp_label)
        current_lp = sum(1 for item in self.items_received if item.item == 565_000000)
        self.lp_label.text = f"LP: {current_lp}/{self.required_lp}"

    def run_gui(self):
        """Import kivy UI system and start running it as self.ui_task."""
        from kvui import GameManager

        class LOLManager(GameManager):
            logging_pairs = [
                ("Client", "Archipelago")
            ]
            base_title = "Archipelago LoL Client"

        self.ui = LOLManager(self)
        self.ui_task = asyncio.create_task(self.ui.async_run(), name="UI")


async def game_watcher(ctx: LOLContext):
    from worlds.lol.Locations import lookup_id_to_name
    while not ctx.exit_event.is_set():
        if ctx.syncing == True:
            sync_msg = [{'cmd': 'Sync'}]
            if ctx.locations_checked:
                sync_msg.append({"cmd": "LocationChecks", "locations": list(ctx.locations_checked)})
            await ctx.send_msgs(sync_msg)
            ctx.syncing = False
        sending = []
        victory = False
        for root, dirs, files in os.walk(ctx.game_communication_path):
            for file in files:
                if file.startswith("send"):
                    st = file[4:]
                    if st == "nil":
                        continue
                    if st.isdigit():
                        sending.append(int(st))
                if file.find("victory") > -1:
                    victory = True

        # Guard against stale/foreign send files from previous sessions or slots.
        # Only location IDs that belong to this slot's active location universe are valid.
        active_ids = ctx.missing_locations | ctx.checked_locations
        if active_ids != ctx._last_active_location_ids_written:
            ctx._last_active_location_ids_written = set(active_ids)
            active_location_ids = {
                lookup_id_to_name[loc_id]: loc_id
                for loc_id in sorted(active_ids)
                if loc_id in lookup_id_to_name
            }
            active_locations = list(active_location_ids.keys())
            try:
                with open(os.path.join(ctx.game_communication_path, "Active_Location_IDs.cfg"), 'w') as f:
                    f.write(str(active_location_ids))
            except Exception:
                pass
            try:
                with open(os.path.join(ctx.game_communication_path, "Active_Locations.cfg"), 'w') as f:
                    f.write(str(active_locations))
            except Exception:
                pass

        sending_set_pre_filter = set(sending)
        if active_ids:
            sending = [loc_id for loc_id in sending if loc_id in active_ids]
        else:
            # No active location universe yet: drop all queued send files as stale.
            sending = []
        stale_ids = sending_set_pre_filter - set(sending)
        for stale_id in stale_ids:
            stale_path = os.path.join(ctx.game_communication_path, f"send{stale_id}")
            try:
                os.remove(stale_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass

        # Deduplicate while preserving deterministic ordering for cfg/messages.
        sending = sorted(set(sending))
        # Win Completes Champion: auto-send all sibling locations when nexus is destroyed
        if ctx.win_completes_champion:
            all_active = ctx.missing_locations | ctx.checked_locations
            new_sends = []
            for loc_id in list(sending):
                # "Enemy Nexus Destroyed" locations have offset 10 in the ID scheme
                if loc_id in lookup_id_to_name and "Enemy Nexus Destroyed" in lookup_id_to_name[loc_id]:
                    champ_bucket = (loc_id - 566_000000) // 100
                    for sibling_id, sibling_name in lookup_id_to_name.items():
                        sibling_relative = sibling_id - 566_000000
                        if (sibling_relative > 0
                                and sibling_relative // 100 == champ_bucket
                                and sibling_id not in sending
                                and sibling_id not in new_sends
                                and sibling_id in all_active):
                            sibling_path = os.path.join(ctx.game_communication_path, f"send{sibling_id}")
                            if not os.path.exists(sibling_path):
                                with open(sibling_path, 'w') as f:
                                    pass
                            new_sends.append(sibling_id)
            sending.extend(new_sends)

        sending_set = set(sending)
        ctx.locations_checked = sending
        if ctx.ui:
            await ctx.draw_lp_counter()
        # Only write cfg and send LocationChecks when the set has actually changed
        if sending_set != ctx._last_locations_sent:
            ctx._last_locations_sent = sending_set
            try:
                with open(os.path.join(ctx.game_communication_path, "Checked_Locations.cfg"), 'w') as f:
                    f.write(str(list(ctx.locations_checked)))
            except Exception:
                pass
            message = [{"cmd": 'LocationChecks', "locations": sending}]
            await ctx.send_msgs(message)
        if not ctx.finished_game and victory:
            await ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
            ctx.finished_game = True
        await asyncio.sleep(0.1)


def launch():
    async def main(args):
        ctx = LOLContext(args.connect, args.password)
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
        if tracker_loaded:
            ctx.run_generator()
        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()
        progression_watcher = asyncio.create_task(
            game_watcher(ctx), name="LOLProgressionWatcher")

        await ctx.exit_event.wait()
        ctx.server_address = None

        await progression_watcher

        await ctx.shutdown()

    import colorama

    parser = get_base_parser(description="LoL Client, for text interfacing.")

    args, rest = parser.parse_known_args()
    colorama.init()
    asyncio.run(main(args))
    colorama.deinit()
