"""
main.py (wersja Android/Kivy)
------------------------------
Mobilny interfejs do zarządzania tablicami rejestracyjnymi w kamerach
ANPR Hikvision (ISAPI). Ta sama logika komunikacji z kamerą co w wersji
na Windows (isapi_client.py bez zmian), ale interfejs przepisany w Kivy
(tkinter z wersji desktopowej nie działa na Androidzie).

4 zakładki: Kamery / Dodaj / Usuń / Przeglądaj - taki sam zestaw funkcji
jak w wersji na Windows.
"""

import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.core.clipboard import Clipboard

import config_store
from isapi_client import (
    ISAPIClient, ISAPIError, CameraProfile,
    LIST_TYPE_ALLOW, LIST_TYPE_BLOCK,
)

LIST_TYPE_LABELS = {
    LIST_TYPE_ALLOW: "Dozwolona (allowlist)",
    LIST_TYPE_BLOCK: "Zablokowana (blocklist)",
}
LABEL_TO_LIST_TYPE = {v: k for k, v in LIST_TYPE_LABELS.items()}


def _autosize_label(label: Label):
    """Sprawia, że Label zawija tekst do szerokości i rośnie w pionie."""
    def _update(instance, value):
        instance.text_size = (instance.width, None)
        instance.texture_update()
        instance.height = instance.texture_size[1]
    label.bind(width=_update, texture_size=lambda i, v: setattr(i, "height", v[1]))
    return label


# ============================================================================
# Wspólna lista kamer z checkboxami (Dodaj / Usuń)
# ============================================================================
class CameraChecklistMobile(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, spacing=dp(2), **kwargs)
        self.app = app
        self.sort_mode = "name"
        self.check_vars = {}
        self.bind(minimum_height=self.setter("height"))

        toolbar = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(4))
        toolbar.add_widget(Label(text="Sortuj:", size_hint_x=None, width=dp(60)))
        toolbar.add_widget(Button(text="Nazwa", on_release=lambda *_: self._set_sort("name")))
        toolbar.add_widget(Button(text="Adres IP", on_release=lambda *_: self._set_sort("ip")))
        self.add_widget(toolbar)

        self.list_container = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.list_container.bind(minimum_height=self.list_container.setter("height"))
        self.add_widget(self.list_container)

        self.app.register_camera_change_listener(self.refresh)
        self.refresh()

    @staticmethod
    def _key(cam: CameraProfile) -> str:
        return f"{cam.name}|{cam.ip}|{cam.port}"

    @staticmethod
    def _ip_sort_key(cam: CameraProfile):
        try:
            return tuple(int(p) for p in cam.ip.strip().split("."))
        except ValueError:
            return (999, 999, 999, 999)

    def _set_sort(self, mode):
        self.sort_mode = mode
        self.refresh()

    def refresh(self):
        self.list_container.clear_widgets()
        cams = self.app.cameras
        if self.sort_mode == "ip":
            cams = sorted(cams, key=self._ip_sort_key)
        else:
            cams = sorted(cams, key=lambda c: c.name.lower())

        old_vars = self.check_vars
        self.check_vars = {}
        if not cams:
            self.list_container.add_widget(Label(
                text="Brak skonfigurowanych kamer.", size_hint_y=None, height=dp(32)))
            return

        for cam in cams:
            key = self._key(cam)
            row = BoxLayout(size_hint_y=None, height=dp(40))
            cb = CheckBox(size_hint_x=None, width=dp(44),
                          active=old_vars[key].active if key in old_vars else False)
            row.add_widget(cb)
            lbl = Label(text=f"{cam.name} ({cam.ip}:{cam.port})", halign="left", valign="middle")
            lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
            row.add_widget(lbl)
            self.list_container.add_widget(row)
            self.check_vars[key] = cb
        self._sorted_cams = cams

    def selected_cameras(self):
        result = []
        for cam in getattr(self, "_sorted_cams", []):
            cb = self.check_vars.get(self._key(cam))
            if cb and cb.active:
                result.append(cam)
        return result

    def select_all(self):
        for cb in self.check_vars.values():
            cb.active = True

    def select_none(self):
        for cb in self.check_vars.values():
            cb.active = False


# ============================================================================
# Zakładka: Kamery
# ============================================================================
class CamerasTabContent(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", padding=dp(8), spacing=dp(6), **kwargs)
        self.app = app

        self.list_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.list_box)
        self.add_widget(scroll)

        self.add_widget(Button(text="Dodaj kamerę", size_hint_y=None, height=dp(48),
                                on_release=lambda *_: self.open_camera_dialog()))

        self.refresh()

    def refresh(self):
        self.list_box.clear_widgets()
        for cam in self.app.cameras:
            row = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(4))
            info = Label(text=f"{cam.name}\n{cam.ip}:{cam.port}", halign="left", valign="middle")
            info.bind(size=lambda inst, val: setattr(inst, "text_size", val))
            row.add_widget(info)
            row.add_widget(Button(text="Edytuj", size_hint_x=None, width=dp(80),
                                   on_release=lambda *_, c=cam: self.open_camera_dialog(c)))
            row.add_widget(Button(text="Test", size_hint_x=None, width=dp(70),
                                   on_release=lambda *_, c=cam: self.test_connection(c)))
            row.add_widget(Button(text="Usuń", size_hint_x=None, width=dp(70),
                                   on_release=lambda *_, c=cam: self.remove_camera(c)))
            self.list_box.add_widget(row)

    def open_camera_dialog(self, camera: CameraProfile = None):
        content = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(8))
        fields = {}

        def add_field(label, key, value="", password=False):
            content.add_widget(Label(text=label, size_hint_y=None, height=dp(22), halign="left"))
            ti = TextInput(text=value, multiline=False, password=password,
                            size_hint_y=None, height=dp(40))
            content.add_widget(ti)
            fields[key] = ti

        add_field("Nazwa (opis):", "name", camera.name if camera else "")
        add_field("Adres IP:", "ip", camera.ip if camera else "")
        add_field("Port:", "port", str(camera.port) if camera else "80")
        add_field("Użytkownik:", "username", camera.username if camera else "")
        add_field("Hasło:", "password", camera.password if camera else "", password=True)
        add_field("Kanał (ID):", "channel_id", camera.channel_id if camera else "1")

        https_row = BoxLayout(size_hint_y=None, height=dp(40))
        https_cb = CheckBox(active=camera.use_https if camera else False, size_hint_x=None, width=dp(44))
        https_row.add_widget(https_cb)
        https_row.add_widget(Label(text="Użyj HTTPS", halign="left"))
        content.add_widget(https_row)

        error_label = Label(text="", size_hint_y=None, height=dp(24), color=(1, 0.3, 0.3, 1))
        content.add_widget(error_label)

        btn_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
        content.add_widget(btn_row)

        popup = Popup(title="Kamera" if camera is None else "Edytuj kamerę",
                       content=content, size_hint=(0.92, 0.85))

        def on_save(*_):
            name = fields["name"].text.strip()
            ip = fields["ip"].text.strip()
            username = fields["username"].text.strip()
            try:
                port = int(fields["port"].text.strip())
            except ValueError:
                error_label.text = "Port musi być liczbą."
                return
            if not name or not ip or not username:
                error_label.text = "Uzupełnij nazwę, adres IP i użytkownika."
                return

            new_cam = CameraProfile(
                name=name, ip=ip, port=port, username=username,
                password=fields["password"].text, use_https=https_cb.active,
                channel_id=fields["channel_id"].text.strip() or "1",
            )
            if camera is None:
                self.app.cameras.append(new_cam)
            else:
                for i, c in enumerate(self.app.cameras):
                    if c is camera:
                        self.app.cameras[i] = new_cam
                        break
            self.app.notify_cameras_changed()
            self.refresh()
            popup.dismiss()

        btn_row.add_widget(Button(text="Zapisz", on_release=on_save))
        btn_row.add_widget(Button(text="Anuluj", on_release=lambda *_: popup.dismiss()))
        popup.open()

    def remove_camera(self, camera: CameraProfile):
        self.app.cameras = [c for c in self.app.cameras if c is not camera]
        self.app.notify_cameras_changed()
        self.refresh()

    def test_connection(self, camera: CameraProfile):
        client = self.app.make_client(camera)

        def worker():
            try:
                client.test_connection()
                self.app.log(f"Test połączenia OK: {camera.name}")
                Clock.schedule_once(lambda dt: self.app.show_popup_message(
                    "Test połączenia", f"Połączenie z '{camera.name}' udane."), 0)
            except ISAPIError as e:
                Clock.schedule_once(lambda dt: self.app.show_popup_message(
                    "Test połączenia", f"Błąd: {e}"), 0)

        threading.Thread(target=worker, daemon=True).start()


# ============================================================================
# Zakładka: Dodaj tablicę
# ============================================================================
class AddPlateTabContent(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", padding=dp(8), spacing=dp(6), **kwargs)
        self.app = app

        self.plate_input = TextInput(hint_text="Numer tablicy", multiline=False,
                                      size_hint_y=None, height=dp(44))
        self.add_widget(self.plate_input)

        self.list_type_spinner = Spinner(text=LIST_TYPE_LABELS[LIST_TYPE_ALLOW],
                                          values=list(LIST_TYPE_LABELS.values()),
                                          size_hint_y=None, height=dp(44))
        self.add_widget(self.list_type_spinner)

        self.add_widget(Label(text="Wybierz kamery:", size_hint_y=None, height=dp(24), halign="left"))
        checklist_scroll = ScrollView(size_hint_y=0.35)
        self.checklist = CameraChecklistMobile(app)
        checklist_scroll.add_widget(self.checklist)
        self.add_widget(checklist_scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        btn_row.add_widget(Button(text="Zaznacz wszystkie", on_release=lambda *_: self.checklist.select_all()))
        btn_row.add_widget(Button(text="Odznacz", on_release=lambda *_: self.checklist.select_none()))
        self.add_widget(btn_row)

        self.add_widget(Button(text="Dodaj do wybranych kamer", size_hint_y=None, height=dp(50),
                                on_release=lambda *_: self.do_add()))

        self.results_label = _autosize_label(Label(text="", halign="left", valign="top",
                                                     size_hint_y=None))
        results_scroll = ScrollView()
        results_scroll.add_widget(self.results_label)
        self.add_widget(results_scroll)

    def do_add(self):
        plate = self.plate_input.text.strip().upper()
        if not plate:
            return
        cameras = self.checklist.selected_cameras()
        if not cameras:
            return
        list_type = LABEL_TO_LIST_TYPE[self.list_type_spinner.text]

        lines = {cam.name: f"{cam.name}: w trakcie..." for cam in cameras}

        def render():
            self.results_label.text = "\n".join(lines[c.name] for c in cameras)

        render()

        def worker(cam: CameraProfile):
            client = self.app.make_client(cam)
            try:
                client.add_plate(plate, list_type)
                lines[cam.name] = f"{cam.name}: OK - dodano {plate}"
            except ISAPIError as e:
                lines[cam.name] = f"{cam.name}: BŁĄD - {e}"
            Clock.schedule_once(lambda dt: render(), 0)

        for cam in cameras:
            threading.Thread(target=worker, args=(cam,), daemon=True).start()


# ============================================================================
# Zakładka: Usuń tablicę
# ============================================================================
class DeletePlateTabContent(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", padding=dp(8), spacing=dp(6), **kwargs)
        self.app = app

        self.plate_input = TextInput(hint_text="Numer tablicy do usunięcia", multiline=False,
                                      size_hint_y=None, height=dp(44))
        self.add_widget(self.plate_input)

        self.add_widget(Label(text="Wybierz kamery:", size_hint_y=None, height=dp(24), halign="left"))
        checklist_scroll = ScrollView(size_hint_y=0.4)
        self.checklist = CameraChecklistMobile(app)
        checklist_scroll.add_widget(self.checklist)
        self.add_widget(checklist_scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        btn_row.add_widget(Button(text="Zaznacz wszystkie", on_release=lambda *_: self.checklist.select_all()))
        btn_row.add_widget(Button(text="Odznacz", on_release=lambda *_: self.checklist.select_none()))
        self.add_widget(btn_row)

        self.add_widget(Button(text="Usuń z wybranych kamer", size_hint_y=None, height=dp(50),
                                on_release=lambda *_: self.do_delete()))

        self.results_label = _autosize_label(Label(text="", halign="left", valign="top",
                                                     size_hint_y=None))
        results_scroll = ScrollView()
        results_scroll.add_widget(self.results_label)
        self.add_widget(results_scroll)

    def do_delete(self):
        plate = self.plate_input.text.strip().upper()
        if not plate:
            return
        cameras = self.checklist.selected_cameras()
        if not cameras:
            return

        lines = {cam.name: f"{cam.name}: w trakcie..." for cam in cameras}

        def render():
            self.results_label.text = "\n".join(lines[c.name] for c in cameras)

        render()

        def worker(cam: CameraProfile):
            client = self.app.make_client(cam)
            try:
                client.delete_plate(plate)
                lines[cam.name] = f"{cam.name}: OK - usunięto {plate}"
            except ISAPIError as e:
                lines[cam.name] = f"{cam.name}: BŁĄD - {e}"
            Clock.schedule_once(lambda dt: render(), 0)

        for cam in cameras:
            threading.Thread(target=worker, args=(cam,), daemon=True).start()


# ============================================================================
# Zakładka: Przeglądaj
# ============================================================================
class BrowseTabContent(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", padding=dp(8), spacing=dp(6), **kwargs)
        self.app = app
        self.position = 0
        self.page_size = 40

        self.camera_spinner = Spinner(text="", values=[], size_hint_y=None, height=dp(44))
        self.add_widget(self.camera_spinner)

        self.filter_input = TextInput(hint_text="Filtr - numer tablicy", multiline=False,
                                       size_hint_y=None, height=dp(44))
        self.add_widget(self.filter_input)

        self.list_type_spinner = Spinner(text="Wszystkie",
                                          values=["Wszystkie"] + list(LIST_TYPE_LABELS.values()),
                                          size_hint_y=None, height=dp(44))
        self.add_widget(self.list_type_spinner)

        self.add_widget(Button(text="Pobierz listę", size_hint_y=None, height=dp(46),
                                on_release=lambda *_: self.do_search()))

        self.results_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.results_box.bind(minimum_height=self.results_box.setter("height"))
        results_scroll = ScrollView()
        results_scroll.add_widget(self.results_box)
        self.add_widget(results_scroll)

        nav_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        nav_row.add_widget(Button(text="< Poprzednia", on_release=lambda *_: self.prev_page()))
        nav_row.add_widget(Button(text="Następna >", on_release=lambda *_: self.next_page()))
        self.add_widget(nav_row)

        self.status_label = Label(text="", size_hint_y=None, height=dp(28))
        self.add_widget(self.status_label)

        self.app.register_camera_change_listener(self.refresh_cameras)
        self.refresh_cameras()

    def refresh_cameras(self):
        names = [c.name for c in self.app.cameras]
        self.camera_spinner.values = names
        if names and self.camera_spinner.text not in names:
            self.camera_spinner.text = names[0]

    def _selected_camera(self):
        for c in self.app.cameras:
            if c.name == self.camera_spinner.text:
                return c
        return None

    def do_search(self, keep_position=False):
        cam = self._selected_camera()
        if not cam:
            self.status_label.text = "Wybierz kamerę."
            return
        if not keep_position:
            self.position = 0

        list_type = LABEL_TO_LIST_TYPE.get(self.list_type_spinner.text)  # None = "Wszystkie"
        plate_filter = self.filter_input.text.strip()
        client = self.app.make_client(cam)
        self.status_label.text = "Pobieranie..."

        def worker():
            try:
                result = client.search_plates(list_type=list_type, plate_number=plate_filter,
                                               position=self.position, max_results=self.page_size)
                Clock.schedule_once(lambda dt: self._show_results(result), 0)
            except ISAPIError as e:
                msg = str(e)
                Clock.schedule_once(lambda dt: self._show_error(msg), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _show_results(self, result):
        self.results_box.clear_widgets()
        records = result.get("records", [])
        total = result.get("total", len(records))

        if not records:
            self.status_label.text = f"Brak wyników. Pozycja {self.position}, razem: {total}"
            return

        for rec in records:
            plate = rec.get("LicensePlate", "")
            lt = rec.get("listType", "")
            lt_label = LIST_TYPE_LABELS.get(lt, lt)
            row = BoxLayout(size_hint_y=None, height=dp(44))
            lbl = Label(text=f"{plate}\n[{lt_label}]", halign="left", valign="middle")
            lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
            row.add_widget(lbl)
            row.add_widget(Button(text="Kopiuj", size_hint_x=None, width=dp(80),
                                   on_release=lambda *_, p=plate: self._copy(p)))
            self.results_box.add_widget(row)

        self.status_label.text = f"Wyświetlono {len(records)} (pozycja {self.position}, razem {total})"

    def _copy(self, plate):
        Clipboard.copy(plate)
        self.status_label.text = f"Skopiowano do schowka: {plate}"

    def _show_error(self, msg):
        self.status_label.text = f"Błąd: {msg}"

    def next_page(self):
        self.position += self.page_size
        self.do_search(keep_position=True)

    def prev_page(self):
        self.position = max(0, self.position - self.page_size)
        self.do_search(keep_position=True)


# ============================================================================
# Główna aplikacja
# ============================================================================
class ANPRApp(App):
    def build(self):
        self.title = "ANPR Manager"
        self.cameras = config_store.load_cameras()
        self._camera_listeners = []
        self.log_lines = []

        root = BoxLayout(orientation="vertical")

        tabs = TabbedPanel(do_default_tab=False, tab_pos="top_mid")

        cameras_item = TabbedPanelItem(text="Kamery")
        cameras_item.add_widget(CamerasTabContent(self))
        tabs.add_widget(cameras_item)

        add_item = TabbedPanelItem(text="Dodaj")
        add_item.add_widget(AddPlateTabContent(self))
        tabs.add_widget(add_item)

        delete_item = TabbedPanelItem(text="Usuń")
        delete_item.add_widget(DeletePlateTabContent(self))
        tabs.add_widget(delete_item)

        browse_item = TabbedPanelItem(text="Przeglądaj")
        browse_item.add_widget(BrowseTabContent(self))
        tabs.add_widget(browse_item)

        root.add_widget(tabs)

        # Mały panel logów na dole (diagnostyka)
        self.log_label = _autosize_label(Label(text="", halign="left", valign="top",
                                                 size_hint_y=None, font_size=dp(11)))
        log_scroll = ScrollView(size_hint_y=None, height=dp(90))
        log_scroll.add_widget(self.log_label)
        root.add_widget(log_scroll)

        return root

    def log(self, msg: str):
        def _do(dt):
            self.log_lines.append(msg)
            self.log_lines = self.log_lines[-40:]
            self.log_label.text = "\n".join(self.log_lines)
        Clock.schedule_once(_do, 0)

    def register_camera_change_listener(self, fn):
        self._camera_listeners.append(fn)

    def notify_cameras_changed(self):
        config_store.save_cameras(self.cameras)
        for fn in self._camera_listeners:
            fn()

    def make_client(self, camera: CameraProfile) -> ISAPIClient:
        return ISAPIClient(camera, log_callback=self.log)

    def show_popup_message(self, title, message):
        content = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
        lbl = _autosize_label(Label(text=message, halign="left", valign="top", size_hint_y=None))
        content.add_widget(lbl)
        popup = Popup(title=title, content=content, size_hint=(0.85, 0.5))
        close_btn = Button(text="OK", size_hint_y=None, height=dp(44))
        content.add_widget(close_btn)
        close_btn.bind(on_release=lambda *_: popup.dismiss())
        popup.open()


if __name__ == "__main__":
    ANPRApp().run()
