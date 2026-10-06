# SPDX-License-Identifier: GPL-3.0-only
"""Sequential, resumable multi-game work; no concurrent OVR Port workspaces."""
import json
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from bridge import ROOT, write_json, settings_text, valid_package, artwork_candidates, refresh_value
from storage_ui import StoragePicker
from desktop_ui import ScrollPage, BG

def run_batch(items, action, bridge, serial, credentials, add_steam, save, auto_art=False, storage_id='internal', transport='usb'):
    for index, item in enumerate(items, 1):
        bridge.progress_prefix=f'Game {index}/{len(items)} · {item["options"].get("title") or item["package"]}: '
        bridge.log(f'Batch {index}/{len(items)}: {item["package"]} — {action}')
        item['error']=''
        try:
            item['status']='Working';save()
            if action=='convert':
                backup=item.get('backup')
                if not backup:
                    backup=str(bridge.backup(serial,item['package']))
                    item['backup']=backup;save()
                # Successful builds are reused, not reconverted on retry.
                if not item.get('build'):
                    item['build']=str(bridge.convert(backup,**item['options']))
                item['status']='Ready'
                if auto_art and not item['options']['steam_id']:
                    try:
                        game=json.loads((Path(item['build'])/'game.json').read_text())
                        item['candidates']=artwork_candidates(game['title'])
                        if not item['options']['title']:item['options']['title']=game['title']
                    except Exception as exc:bridge.log('Artwork search unavailable: '+str(exc))
            else:
                folder=Path(item['build'])
                game=json.loads((folder/'game.json').read_text())
                if game['package']!=item['package']:raise ValueError('Build/package mismatch')
                game.update({k:v for k,v in item['options'].items() if k!='title' or v.strip()})
                write_json(folder/'game.json',game)
                with bridge.connect(**credentials) as client:
                    bridge.install(folder,client,add_steam,item['options']['steam_id'],storage_id,transport)
                item['status']='Installed'
        except Exception as exc:
            item['status']='Failed';item['error']=str(exc)
            bridge.log(f'FAILED {item["package"]}: {exc}')
        finally:
            bridge.progress_prefix=''
        save()
    return items

class BatchWindow(tk.Toplevel):
    def __init__(self, parent, packages=(), serial=None, path=None):
        super().__init__(parent);self.parent=parent;self.serial=serial
        self.title('Quest2Frame · Transfer queue');self.geometry('1180x720');self.minsize(960,600);self.configure(bg=BG)
        self.path=Path(path) if path else ROOT/'games/batches'/f'{uuid.uuid4().hex}.json'
        defaults=parent.options();self.items=[];self.active=None
        if path:
            data=json.loads(self.path.read_text());self.items=data['items'];self.serial=data.get('serial')
            for item in self.items:
                valid_package(item['package']);settings_text(item['options']['scale'])
                if item['status']=='Working':item['status']='Interrupted'
        else:
            for package in packages:
                label=parent.package_names.get(package,'')
                opts=dict(defaults,title=label if label!=package else '',steam_id='')
                self.items.append(dict(package=package,options=opts,status='Queued',error='',backup='',build=''))
        footer=ttk.Frame(self,padding=(24,12));footer.pack(side='bottom',fill='x')
        self.scroll_page=ScrollPage(self);self.scroll_page.pack(fill='both',expand=True)
        box=self.scroll_page.body
        ttk.Label(box,text='Your transfer queue',style='Title.TLabel').pack(anchor='w')
        ttk.Label(box,text='Review each game, convert your selection, then install on Frame.',style='Muted.TLabel').pack(anchor='w',pady=(3,6))
        ttk.Label(box,text='Ctrl/Shift selects multiple rows. Settings below apply only to the highlighted editor game.').pack(anchor='w',pady=5)
        table=ttk.Frame(box);table.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(table,columns=('package','status'),show='headings',selectmode='extended',height=4)
        self.tree.heading('package',text='Game / Quest package');self.tree.heading('status',text='Status')
        self.tree.column('package',width=630);self.tree.column('status',width=150)
        scrollbar=ttk.Scrollbar(table,command=self.tree.yview);scrollbar.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set);self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.selected)
        ttk.Button(box,text='Select all games',command=lambda:self.tree.selection_set(self.tree.get_children())).pack(anchor='w',pady=5)
        form=ttk.LabelFrame(box,text='Per-game settings — Save before switching games',padding=10);form.pack(fill='x')
        self.editing=tk.StringVar(value='Select a game to edit');ttk.Label(form,textvariable=self.editing).grid(row=0,column=0,columnspan=4,sticky='w')
        self.values={name:tk.StringVar() for name in ('title','scale','steam_id')}
        self.values['refresh_rate']=tk.StringVar(value='Default')
        self.values['headset']=tk.StringVar(value='Default')
        for row,(name,label) in enumerate((('title','Title (blank = APK title)'),('scale','Resolution 50–200%'),('steam_id','Steam artwork App ID')),1):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',padx=5)
            ttk.Entry(form,textvariable=self.values[name],width=45).grid(row=row,column=1,sticky='ew',pady=3)
        form.columnconfigure(1,weight=1)
        self.controllers=tk.BooleanVar();self.foveation=tk.BooleanVar()
        self.rect_fix=tk.BooleanVar(value=False);self.open_dispatcher=tk.BooleanVar(value=False)
        ttk.Checkbutton(form,text='Physical controllers',variable=self.controllers).grid(row=1,column=2,padx=10)
        ttk.Checkbutton(form,text='Quest foveation fix',variable=self.foveation).grid(row=2,column=2,padx=10)
        ttk.Checkbutton(form,text='Fix black screen (rebuild required)',variable=self.rect_fix).grid(row=5,column=2,padx=10)
        ttk.Checkbutton(form,text='Open-source OpenXR dispatcher',variable=self.open_dispatcher).grid(row=6,column=2,padx=10)
        ttk.Button(form,text='Save game settings',command=self.save_editor).grid(row=3,column=2,padx=10)
        ttk.Button(form,text='Find artwork by title',command=self.search_art).grid(row=4,column=0,pady=5)
        self.matches=ttk.Combobox(form,state='readonly',width=50);self.matches.grid(row=4,column=1,columnspan=2,sticky='ew')
        self.candidates=[];self.matches.bind('<<ComboboxSelected>>',self.choose_art)
        ttk.Label(form,text='Headset refresh rate (Hz)').grid(row=5,column=0,sticky='w')
        ttk.Combobox(form,textvariable=self.values['refresh_rate'],values=('Default','72','80','90','120','144'),state='readonly',width=12).grid(row=5,column=1,sticky='w')
        ttk.Label(form,text='Reported headset (rebuild required)').grid(row=6,column=0,sticky='w')
        ttk.Combobox(form,textvariable=self.values['headset'],values=('Default','Quest 2','Quest 3'),state='readonly',width=12).grid(row=6,column=1,sticky='w')
        self.add_steam=tk.BooleanVar(value=parent.add_steam.get())
        self.wireless_install=tk.BooleanVar(value=parent.wireless_install.get())
        self.storage_picker=StoragePicker(box,parent);self.storage_picker.pack(fill='x',pady=8)
        ttk.Checkbutton(box,text='Install wirelessly over Wi-Fi (SSH/SFTP); Quest can stay connected to PC',variable=self.wireless_install).pack(anchor='w')
        ttk.Checkbutton(box,text='Add each installed game to Steam (Steam restarts for each game)',variable=self.add_steam).pack(anchor='w',pady=8)
        buttons=ttk.Frame(footer);buttons.pack(fill='x')
        ttk.Button(buttons,text='Back up & convert selected',style='Primary.TButton',command=lambda:self.start('convert')).pack(side='left',padx=3)
        ttk.Button(buttons,text='Install selected ready games',style='Primary.TButton',command=lambda:self.start('install')).pack(side='left',padx=3)
        ttk.Button(buttons,text='Add saved conversions…',command=self.add_builds).pack(side='left',padx=3)
        self.message=tk.StringVar(value='Keep Quest connected throughout the conversion batch.')
        ttk.Label(footer,textvariable=self.message,wraplength=880).pack(anchor='w',pady=8)
        ttk.Label(footer,textvariable=parent.progress_text,wraplength=880).pack(fill='x')
        ttk.Progressbar(footer,variable=parent.progress_value,maximum=100).pack(fill='x',pady=4)
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.refresh();self.tree.selection_set(self.tree.get_children());self.save();self.poll_id=self.after(250,self.poll)

    def save(self):write_json(self.path,dict(serial=self.serial,items=self.items))
    def refresh(self):
        for index,item in enumerate(self.items):
            title=item['options'].get('title','')
            ident=str(index);values=((title+' — ' if title else '')+item['package'],item['status'])
            if self.tree.exists(ident):self.tree.item(ident,values=values)
            else:self.tree.insert('', 'end',iid=ident,values=values)
    def poll(self):self.refresh();self.poll_id=self.after(250,self.poll)
    def selected(self,event=None):
        if self.parent.busy:return
        selection=self.tree.selection()
        if not selection:return
        focus=self.tree.focus();ident=focus if focus in selection else selection[0]
        self.active=int(ident);item=self.items[self.active]
        self.editing.set(item['package'])
        for k,v in self.values.items():v.set(str((item['options'].get(k) or 'Default') if k in ('refresh_rate','headset') else item['options'][k]))
        self.controllers.set(item['options']['controllers']);self.foveation.set(item['options']['foveation'])
        self.rect_fix.set(item['options'].get('rect_fix',False))
        self.open_dispatcher.set(item['options'].get('open_dispatcher',False))
        self.candidates=item.get('candidates',[])
        self.matches.configure(values=[f"{m['title']} — {m['id']}" for m in self.candidates])
        self.matches.set('Choose a candidate and Save' if self.candidates else 'Enter a title to search for artwork')
        self.message.set(item['error'] or item['status'])
    def save_editor(self):
        if self.parent.busy or self.active is None:return False
        try:
            opts={k:v.get().strip() for k,v in self.values.items()};opts['scale']=float(opts['scale'])
            opts['refresh_rate']=refresh_value(opts['refresh_rate'])
            from headset_profile import headset_value
            opts['headset']=headset_value(opts['headset'])
            settings_text(opts['scale'],refresh_rate=opts['refresh_rate'])
            if opts['steam_id'] and not opts['steam_id'].isdigit():raise ValueError('Steam App ID must be numeric')
            opts.update(controllers=self.controllers.get(),foveation=self.foveation.get(),rect_fix=self.rect_fix.get(),open_dispatcher=self.open_dispatcher.get())
            from bridge import check_dispatcher_headset
            check_dispatcher_headset(opts['open_dispatcher'],opts['headset'])
        except ValueError as exc:messagebox.showerror('Invalid settings',str(exc),parent=self);return False
        self.items[self.active]['options']=opts;self.save();return True
    def search_art(self):
        if not self.save_editor():return
        title=self.values['title'].get().strip()
        if not title:self.message.set('Enter a title to search. Blank titles use APK labels during conversion.');return
        active=self.active
        def work():
            try:return artwork_candidates(title),None
            except Exception as exc:return [],str(exc)
        def done(result):
            matches,error=result
            if active!=self.active:return
            self.candidates=matches;self.matches.configure(values=[f"{m['title']} — {m['id']}" for m in matches])
            self.matches.set('Choose the matching edition' if matches else 'No matches')
            self.message.set(error or 'Choose a candidate, then Save game settings.')
        self.parent.run_job('Searching Steam artwork…',work,done)
    def choose_art(self,event=None):
        index=self.matches.current()
        if not self.parent.busy and 0<=index<len(self.candidates):self.values['steam_id'].set(self.candidates[index]['id'])
    def add_builds(self):
        if self.parent.busy:return
        paths=filedialog.askopenfilenames(parent=self,title='Select saved game manifests',initialdir=ROOT/'games',filetypes=[('Game manifests','game.json')])
        for path in paths:
            try:
                game=json.loads(Path(path).read_text());package=valid_package(game['package'])
                if any(i['package']==package for i in self.items):continue
                self.items.append(dict(package=package,options={k:game[k] for k in ('title','scale','steam_id','controllers','foveation')},build=str(Path(path).parent),backup=game['backup'],status='Ready',error=''))
                self.items[-1]['options']['refresh_rate']=game.get('refresh_rate',0)
                self.items[-1]['options']['headset']=game.get('headset','Default')
                from bridge import rect_fix_value, open_dispatcher_value
                self.items[-1]['options']['rect_fix']=rect_fix_value(game)
                self.items[-1]['options']['open_dispatcher']=open_dispatcher_value(game)
            except Exception as exc:messagebox.showerror('Invalid manifest',str(exc),parent=self)
        self.refresh();self.save()
    def start(self,action):
        if self.parent.busy:return
        if self.active is not None and not self.save_editor():return
        items=[self.items[int(i)] for i in self.tree.selection()]
        items=[i for i in items if (not i.get('build') if action=='convert' else i.get('build') and i['status']!='Installed')]
        if not items:self.message.set('Select unconverted games or ready builds; completed items are skipped.');return
        credentials=self.parent.credentials();add=self.add_steam.get();auto_art=self.parent.auto_art.get()
        transport='wifi' if self.wireless_install.get() else 'usb'
        try:storage_id=self.storage_picker.selected_id()
        except ValueError as exc:messagebox.showerror('Storage',str(exc),parent=self);return
        def work():
            if action=='convert':
                if self.serial not in [s for s,_ in self.parent.bridge.quests()]:raise RuntimeError('Reconnect the original Quest and allow USB debugging.')
            return run_batch(items,action,self.parent.bridge,self.serial,credentials,add,self.save,auto_art,storage_id,transport)
        def done(result):
            failed=sum(i['status']=='Failed' for i in result)
            self.refresh();self.selected()
            self.message.set(f'Batch finished: {len(result)-failed} completed, {failed} failed. '+('Wake Frame, choose Wi-Fi or USB, select ready games, then Install.' if action=='convert' else 'See the log for details.'))
        self.parent.run_job(f'Batch {action}: {len(items)} games. Keep the headset connected…',work,done)
    def close(self):
        if self.parent.busy:messagebox.showinfo('Working','Wait for the batch to finish.',parent=self);return
        if self.active is not None and not self.save_editor():return
        self.after_cancel(self.poll_id)
        self.destroy()
