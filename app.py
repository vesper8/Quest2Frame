# SPDX-License-Identifier: GPL-3.0-only
"""Windows desktop wizard. Passwords are memory-only, never persisted."""
from pathlib import Path
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from bridge import APP, ROOT, Bridge, write_json, settings_text, artwork_candidates, suggested_title
from desktop_ui import DesktopUI

class Wizard(DesktopUI, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Quest → Frame'); self.geometry('960x790'); self.minsize(830, 680)
        self.events = queue.Queue(); self.busy = False; self.build = None; self.serial = None; self.package_list = []
        self.bridge = Bridge(lambda line: self.events.put(('log', line)),
                             lambda stage, percent: self.events.put(('progress', (stage, percent))))
        self.vars = {k: tk.StringVar(value=v) for k, v in {'host':'frame', 'port':'22', 'username':'steamos', 'password':'', 'title':'', 'steam_id':'', 'filter':'', 'scale':'100'}.items()}
        self.controllers = tk.BooleanVar(value=True); self.foveation = tk.BooleanVar(value=True); self.add_steam = tk.BooleanVar(value=True)
        self.auto_art = tk.BooleanVar(value=True); self.art_candidates = []
        self.wireless_install = tk.BooleanVar(value=True)
        self.package_names={};self.package_icons={};self.visible_packages=[]
        config = APP / 'settings.json'
        if config.exists():
            try:
                for k,v in json.loads(config.read_text()).items():
                    if k in ('host','port','username'): self.vars[k].set(v)
            except (ValueError, OSError): pass
        self.build_ui()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.drain)

    def credentials(self):
        return {k:self.vars[k].get() for k in ('host','username','password','port')}

    def manage_installed(self):
        if self.busy:return
        if getattr(self,'installed_window',None) and self.installed_window.winfo_exists():
            self.installed_window.lift();return
        from manage_ui import InstalledGames
        self.installed_window=InstalledGames(self)

    def run_job(self, title, fn, done=None):
        if self.busy: messagebox.showinfo('Working','Wait for the current operation to finish.');return
        self.busy=True;self.lock_actions(True);self.status.set(title);self.show_progress(title,None)
        def worker():
            try: self.events.put(('done',(fn(),done)))
            except Exception as exc: self.events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def show_progress(self, stage, percent):
        mode = 'indeterminate' if percent is None else 'determinate'
        if str(self.progress['mode']) != mode:
            self.progress.stop();self.progress.configure(mode=mode);self.progress_value.set(0)
            if percent is None:self.progress.start(15)
        if percent is not None:self.progress_value.set(max(0,min(100,percent)))
        self.progress_text.set(stage + (' — working…' if percent is None else f' — {percent:.0f}% of this stage'))

    def drain(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='log':
                    self.logbox.configure(state='normal');self.logbox.insert('end',value+'\n');self.logbox.see('end');self.logbox.configure(state='disabled')
                elif kind=='progress':
                    self.show_progress(*value)
                else:
                    self.busy=False;self.lock_actions(False);self.progress.stop()
                    self.progress.configure(mode='determinate')
                    if kind=='error':
                        self.progress_value.set(0);self.progress_text.set('Stopped — operation failed; see error details')
                        self.status.set('Needs attention — see details below.');messagebox.showerror('Quest → Frame',value)
                    else:
                        self.progress_value.set(100);self.progress_text.set('Operation finished — see status and log for results')
                        result,done=value;self.status.set('Ready')
                        if done: done(result)
        except queue.Empty: pass
        self.after(100,self.drain)

    def test_ssh(self):
        credentials=self.credentials()
        def work():
            with self.bridge.connect(**credentials) as client:self.bridge.remote(client,'test -x "$HOME/.local/share/Steam/steamapps/common/Lepton/lepton"')
            write_json(APP/'settings.json',{k:v for k,v in credentials.items() if k!='password'})
        def done(_):
            self.device_status.set('FRAME  /  Verified · '+credentials['host'])
            self.status.set('Frame verified. Connect Quest by USB, then choose your games.')
            self.tabs.select(1)
        self.run_job('Verifying Frame SSH…',work,done)

    def scan(self):
        def work():
            devices=self.bridge.quests()
            if len(devices)!=1: raise RuntimeError('Connect exactly one Quest and accept USB debugging. Found: '+str(len(devices)))
            return devices[0][0],self.bridge.installed_apps(devices[0][0])
        def done(result):
            self.serial,apps=result
            self.package_list=[a['package'] for a in apps];self.package_names={a['package']:a['title'] for a in apps}
            self.package_icons={a['package']:a['icon'] for a in apps if a.get('icon')}
            self.filter_packages();self.status.set(f'{len(self.package_list)} installed apps. Select a game.')
        self.run_job('Scanning Quest…',work,done)

    def filter_packages(self):
        query=self.vars['filter'].get().lower();self.listbox.delete(0,'end')
        self.visible_packages=[]
        for package in self.package_list:
            title=self.package_names.get(package,package)
            if query in package.lower() or query in title.lower():
                self.visible_packages.append(package)
                self.listbox.insert('end',suggested_title(package) if title==package else title,self.package_icons.get(package))
        self.library_count.set(f'{len(self.visible_packages)} of {len(self.package_list)} apps · Quest library')
        self.library_selection_changed()

    def select_game(self):
        if self.busy:return
        indexes=self.listbox.curselection()
        if not indexes: messagebox.showinfo('Select a game','Choose an installed package first.');return
        if len(indexes)>1:
            self.show_batch([self.visible_packages[i] for i in indexes]);return
        self.build=None
        self.ready.set('Convert this selection before installing, or open a saved build.')
        self.package=self.visible_packages[indexes[0]];self.selected.set(self.package_names.get(self.package,self.package)+' — '+self.package)
        label=self.package_names.get(self.package,'')
        self.vars['title'].set(label if label!=self.package else '')
        self.vars['steam_id'].set('')
        self.art_candidates=[];self.art_matches.configure(values=[]);self.art_matches.set('No artwork selected')
        self.tabs.select(2)
        if self.auto_art.get():self.search_artwork(query=self.vars['title'].get() or suggested_title(self.package))

    def show_batch(self,packages=(),path=None):
        if self.busy:return
        if getattr(self,'batch_window',None) and self.batch_window.winfo_exists():
            self.batch_window.lift();return
        from batch import BatchWindow
        try:self.batch_window=BatchWindow(self,packages,self.serial,path)
        except Exception as exc:messagebox.showerror('Batch queue',str(exc))

    def open_batch(self):
        if self.busy:return
        path=filedialog.askopenfilename(title='Open saved batch (Cancel to create an empty queue)',initialdir=ROOT/'games/batches',filetypes=[('Batch queue','*.json')])
        self.show_batch(path=path or None)

    def search_artwork(self, query=None):
        if self.busy:return
        query = query or self.vars['title'].get().strip()
        if not query:
            messagebox.showinfo('Artwork search','Enter a game title first.');return
        title_before=self.vars['title'].get();id_before=self.vars['steam_id'].get()
        self.art_candidates=[];self.art_matches.configure(values=[]);self.art_matches.set('Searching Steam…')
        def work():
            try:return artwork_candidates(query),None
            except Exception as exc:return [],str(exc)
        def done(result):
            if self.vars['title'].get()!=title_before or self.vars['steam_id'].get()!=id_before:
                self.art_matches.set('Details changed — click Find artwork to search again');return
            matches,error=result;self.art_candidates=matches
            self.art_matches.configure(values=[f"{m['title']} — App ID {m['id']}" for m in matches])
            if error:
                self.art_matches.set('Search unavailable — retry or enter an App ID manually')
                self.status.set('Artwork search failed; conversion is still available.')
                self.events.put(('log','Artwork search: '+error))
            elif matches:
                self.art_matches.set('Choose a candidate — verify the game and edition')
                self.status.set(f'{len(matches)} artwork candidates found. Choose one or keep your manual App ID.')
            else:
                self.art_matches.set('No matches — edit the title and search again')
                self.status.set('No artwork match. You can continue without artwork.')
        self.run_job('Searching Steam for artwork candidates…',work,done)

    def choose_artwork(self,event=None):
        index=self.art_matches.current()
        if self.busy or not 0<=index<len(self.art_candidates):return
        candidate=self.art_candidates[index]
        self.vars['steam_id'].set(candidate['id'])
        self.status.set('Artwork selected: '+candidate['title'])

    def options(self):
        options = dict(title=self.vars['title'].get(),scale=float(self.vars['scale'].get()),controllers=self.controllers.get(),foveation=self.foveation.get(),steam_id=self.vars['steam_id'].get())
        from bridge import refresh_value
        options['refresh_rate'] = refresh_value(self.vars['refresh_rate'].get())
        from headset_profile import headset_value
        options['headset'] = headset_value(self.vars['headset'].get())
        options['rect_fix'] = self.rect_fix.get()
        options['open_dispatcher'] = self.open_dispatcher.get()
        settings_text(options['scale'], options['controllers'], options['foveation'], options['refresh_rate'])
        return options

    def convert(self):
        if not self.serial or not hasattr(self,'package'):messagebox.showinfo('Select a game','Scan and select your Quest game first.');return
        try: options=self.options()
        except ValueError: messagebox.showerror('Invalid scale','Enter a number between 50 and 200.');return
        serial,package=self.serial,self.package
        self.run_job('Backing up and converting. Keep Quest connected…',lambda:self.bridge.convert(self.bridge.backup(serial,package),**options),self.build_ready)

    def build_ready(self,folder):
        self.build=Path(folder);game=json.loads((self.build/'game.json').read_text())
        self.package=game['package']
        for k in ('title','scale','steam_id'):self.vars[k].set(str(game[k]))
        self.vars['refresh_rate'].set(str(game.get('refresh_rate') or 'Default'))
        self.vars['headset'].set(game.get('headset','Default'))
        from bridge import rect_fix_value, open_dispatcher_value
        self.rect_fix.set(rect_fix_value(game))
        self.open_dispatcher.set(open_dispatcher_value(game))
        self.controllers.set(game['controllers']);self.foveation.set(game['foveation'])
        self.ready.set(game['title']+' is ready. Choose Wi-Fi or USB installation.');self.selected.set(game['package']);self.tabs.select(3)
        self.status.set('3 · Conversion ready. Wake Frame, then Install.')
        self.art_candidates=[];self.art_matches.configure(values=[]);self.art_matches.set('Click Find artwork to search for this game')
        if self.auto_art.get() and not game.get('steam_id'):
            self.tabs.select(2)
            self.search_artwork(query=game['title'])

    def open_build(self):
        if self.busy:return
        path=filedialog.askopenfilename(title='Open converted game manifest',initialdir=ROOT/'games',filetypes=[('Game manifest','game.json')])
        if path:
            try:self.build_ready(Path(path).parent)
            except Exception as exc:messagebox.showerror('Invalid build',str(exc))

    def rebuild_apk(self):
        if self.busy:return
        if not self.build:
            messagebox.showinfo('Open a build','Open the saved build you want to test first.');return
        try: options=self.options()
        except ValueError as exc:messagebox.showerror('Invalid settings',str(exc));return
        folder=self.build
        self.run_job('Rebuilding APK only. Quest and OBBs are not needed…',
                     lambda:self.bridge.rebuild_apk(folder,**options),self.build_ready)

    def install_apk_only(self):
        if self.busy:return
        if not self.build or not (self.build/'deployment.json').exists():
            messagebox.showinfo('Existing installation required','Open or rebuild an already-installed game. Use the full installer for its first installation.');return
        try:
            from bridge import check_headset_build, check_rect_build, check_dispatcher_build
            game=json.loads((self.build/'game.json').read_text());opts=self.options()
            check_headset_build(game,opts['headset'])
            check_rect_build(game,opts['rect_fix'])
            check_dispatcher_build(game,opts['open_dispatcher'])
            if any(opts[k]!=game.get(k,0 if k=='refresh_rate' else None) for k in ('scale','controllers','foveation','refresh_rate')):
                raise ValueError('Settings changed since this build. Rebuild APK only first, or use Apply settings only.')
        except ValueError as exc:messagebox.showerror('Rebuild required',str(exc));return
        folder=self.build;credentials=self.credentials()
        transport='wifi' if self.wireless_install.get() else 'usb'
        def work():
            with self.bridge.connect(**credentials) as client:self.bridge.install_apk_only(folder,client,transport)
        self.run_job('Updating only the APK on its existing storage device…',work,
                     lambda _:self.status.set('APK updated. OBBs and saves unchanged. Restart the game to test.'))

    def install(self):
        if not self.build:messagebox.showinfo('Convert first','Convert or reopen a game first.');return
        try:options=self.options()
        except ValueError:messagebox.showerror('Invalid scale','Enter a number between 50 and 200.');return
        try:storage_id=self.storage_picker.selected_id()
        except ValueError as exc:messagebox.showerror('Storage',str(exc));return
        credentials=self.credentials();folder=self.build;add=self.add_steam.get();art=self.vars['steam_id'].get()
        transport='wifi' if self.wireless_install.get() else 'usb'
        def work():
            game=json.loads((folder/'game.json').read_text())
            game.update({k:v for k,v in options.items() if k!='title' or v.strip()})
            write_json(folder/'game.json',game)
            with self.bridge.connect(**credentials) as client:return self.bridge.install(folder,client,add,art,storage_id,transport)
        self.run_job('Installing on Frame…',work,lambda _:self.status.set('Installed. Launch the game from your Frame’s Steam library.'))

    def update_settings(self):
        if not self.build: return
        try:opts=self.options()
        except ValueError:messagebox.showerror('Invalid scale','Enter a number between 50 and 200.');return
        credentials=self.credentials();folder=self.build
        def work():
            with self.bridge.connect(**credentials) as client:self.bridge.update_settings(folder,client,opts['scale'],opts['controllers'],opts['foveation'],opts['refresh_rate'],opts['headset'],opts['rect_fix'],opts['open_dispatcher'])
        self.run_job('Updating game settings…',work,lambda _:self.status.set('Settings saved. Restart the game on Frame.'))

    def repair_shortcut(self):
        if not self.build:return
        credentials=self.credentials();folder=self.build;art_id=self.vars['steam_id'].get()
        def work():
            game=json.loads((folder/'game.json').read_text())
            deployment=json.loads((folder/'deployment.json').read_text())
            art=self.bridge.artwork(art_id,folder/'artwork') if art_id else {}
            with self.bridge.connect(**credentials) as client:
                self.bridge.add_shortcut(client,deployment.get('anchor',deployment['remote']),game['title'],deployment['appid'],art)
        self.run_job('Updating Steam shortcut…',work,lambda _:self.status.set('Steam shortcut and artwork updated.'))

    def close(self):
        if self.busy:messagebox.showinfo('Operation in progress','Wait for the current transfer/build to finish before closing.');return
        self.destroy()

if __name__=='__main__': Wizard().mainloop()
