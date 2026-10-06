# SPDX-License-Identifier: GPL-3.0-only
"""Native desktop presentation; transfer and conversion logic stays in app.py."""
import os
import tkinter as tk
from tkinter import ttk, messagebox
from bridge import ROOT, ASSETS
from storage_ui import StoragePicker
from game_list import GameList

BG='#f3f5f7'
SURFACE='#ffffff'
INK='#172936'
MUTED='#647582'
NAV='#142b39'
ACCENT='#147d70'


class ScrollPage(ttk.Frame):
    """Keyboard/wheel/arrow accessible page for small displays and high DPI."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0)
        rail=ttk.Frame(self);rail.pack(side='right',fill='y')
        ttk.Button(rail,text='▴',width=2,command=lambda:self.canvas.yview_scroll(-3,'units')).pack()
        scroll=ttk.Scrollbar(rail,orient='vertical',command=self.canvas.yview)
        scroll.pack(fill='y',expand=True)
        ttk.Button(rail,text='▾',width=2,command=lambda:self.canvas.yview_scroll(3,'units')).pack()
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(fill='both',expand=True)
        self.body=ttk.Frame(self.canvas,padding=(24,12,14,20))
        self.window=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window,width=e.width))

    def wheel(self,event):
        # Each page handles only its own descendants, never another window.
        widget=event.widget
        while widget is not None and widget is not self:
            widget=getattr(widget,'master',None)
        if widget is self and not isinstance(event.widget,(tk.Listbox,tk.Text,ttk.Treeview,ttk.Combobox)):
            self.canvas.yview_scroll(-int(event.delta/120),'units')


class DesktopUI:
    def build_ui(self):
        self.title('Quest2Frame');self.geometry('1280x720');self.minsize(960,600)
        self.configure(bg=BG)
        self.action_buttons=[]
        self._locked=[]
        self.status=tk.StringVar(value='Connect your Frame to get started. Your Quest stays connected by USB.')
        self.device_status=tk.StringVar(value='FRAME  /  Not verified')
        self.library_count=tk.StringVar(value='Scan your Quest to discover installed games')
        self.selection_count=tk.StringVar(value='No games selected')
        self.progress_value=tk.DoubleVar(value=0)
        self.progress_text=tk.StringVar(value='Ready when you are')
        self.vars['refresh_rate']=tk.StringVar(value='Default')
        self.vars['headset']=tk.StringVar(value='Default')
        self.rect_fix=tk.BooleanVar(value=False)
        self.open_dispatcher=tk.BooleanVar(value=False)
        self.theme()

        side=tk.Frame(self,bg=NAV,width=204);side.pack(side='left',fill='y');side.pack_propagate(False)
        logo_source=tk.PhotoImage(master=self,file=str(ASSETS/'assets/quest2frame-logo.png'))
        self.logo_image=logo_source.subsample(max(1,(logo_source.width()+79)//80))
        self.window_icon=logo_source.subsample(max(1,(logo_source.width()+255)//256))
        self.iconphoto(True,self.window_icon)
        tk.Label(side,image=self.logo_image,bg=NAV,bd=0).pack(anchor='w',padx=23,pady=(17,1))
        tk.Label(side,text='Quest2Frame',bg=NAV,fg='white',font=('Segoe UI',17,'bold')).pack(anchor='w',padx=23,pady=(0,25))
        self.nav=[]
        for index,label in enumerate(('01   Connect','02   Choose games','03   Review & convert','04   Install on Frame')):
            button=tk.Button(side,text=label,anchor='w',relief='flat',bd=0,padx=18,pady=14,
                             font=('Segoe UI',10,'bold'),bg=NAV,fg='#bdccd5',activebackground='#264553',activeforeground='white',
                             command=lambda i=index:self.tabs.select(i))
            button.pack(fill='x',padx=10,pady=3);self.nav.append(button)
        tk.Frame(side,height=1,bg='#35505e').pack(fill='x',padx=23,pady=18)
        for label,command in [('Transfer queue',self.show_batch),('Saved queue…',self.open_batch),('Manage Frame games',self.manage_installed),('Backups & builds',lambda:os.startfile(ROOT/'games'))]:
            button=tk.Button(side,text=label,command=command,anchor='w',relief='flat',bd=0,padx=18,pady=10,
                             bg=NAV,fg='#bdccd5',activebackground='#264553',activeforeground='white',font=('Segoe UI',10))
            button.pack(fill='x',padx=10);self.action_buttons.append(button)
        tk.Button(side,text='About & licenses',command=self.about,bg=NAV,fg='#94afbe',relief='flat',bd=0,font=('Segoe UI',9)).pack(side='bottom',anchor='w',padx=22,pady=20)

        main=ttk.Frame(self);main.pack(side='left',fill='both',expand=True)
        top=ttk.Frame(main,padding=(26,18,26,10));top.pack(fill='x')
        ttk.Label(top,text='WORKSPACE',style='Eyebrow.TLabel').pack(side='left')
        ttk.Label(top,textvariable=self.device_status,style='Muted.TLabel').pack(side='right')
        footer=ttk.Frame(main,style='Card.TFrame',padding=(24,12));footer.pack(side='bottom',fill='x')
        line=ttk.Frame(footer,style='Card.TFrame');line.pack(fill='x')
        ttk.Label(line,textvariable=self.progress_text,style='Card.TLabel',wraplength=690).pack(side='left',fill='x',expand=True)
        self.button(line,'Activity log',self.show_log).pack(side='right',padx=(12,0))
        self.progress=ttk.Progressbar(footer,variable=self.progress_value,maximum=100,mode='determinate')
        self.progress.pack(fill='x',pady=(9,7))
        ttk.Label(footer,textvariable=self.status,style='CardMuted.TLabel',wraplength=880).pack(anchor='w')
        footer.bind('<Configure>',lambda e:self.wrap_labels(footer,e.width-48))

        self.tabs=ttk.Notebook(main,style='Pages.TNotebook');self.tabs.pack(fill='both',expand=True)
        self.pages=[];self.scroll_pages=[]
        for label in ('Connect','Choose games','Review & convert','Install on Frame'):
            page=ScrollPage(self.tabs);self.tabs.add(page,text=label);self.scroll_pages.append(page);self.pages.append(page.body)
        self.setup_page();self.quest_page();self.convert_page();self.install_page()
        self.tabs.bind('<<NotebookTabChanged>>',self.navigation_changed)
        self.bind_all('<MouseWheel>',self.page_wheel,add='+')
        self.navigation_changed()
        # A hidden native log window keeps diagnostics available without
        # competing with the workflow for vertical space.
        self.log_window=tk.Toplevel(self);self.log_window.title('Quest2Frame · Activity log')
        self.log_window.geometry('950x450');self.log_window.withdraw()
        self.log_window.protocol('WM_DELETE_WINDOW',self.log_window.withdraw)
        self.logbox=tk.Text(self.log_window,bg=NAV,fg='#dbe8ef',insertbackground='white',font=('Consolas',10),wrap='word',state='disabled',relief='flat',padx=15,pady=15)
        log_scroll=ttk.Scrollbar(self.log_window,command=self.logbox.yview);log_scroll.pack(side='right',fill='y')
        self.logbox.configure(yscrollcommand=log_scroll.set);self.logbox.pack(fill='both',expand=True)

    def theme(self):
        self.option_add('*TCombobox*Listbox.background',SURFACE)
        self.option_add('*TCombobox*Listbox.foreground',INK)
        style=ttk.Style(self);style.theme_use('clam')
        style.configure('.',background=BG,foreground=INK,font=('Segoe UI',10))
        style.configure('TFrame',background=BG)
        style.configure('Card.TFrame',background=SURFACE)
        style.configure('TLabel',background=BG)
        style.configure('Card.TLabel',background=SURFACE)
        style.configure('Muted.TLabel',foreground=MUTED)
        style.configure('CardMuted.TLabel',background=SURFACE,foreground=MUTED,font=('Segoe UI',9))
        style.configure('Eyebrow.TLabel',foreground=ACCENT,font=('Segoe UI',9,'bold'))
        style.configure('Title.TLabel',font=('Segoe UI',25,'bold'))
        style.configure('CardTitle.TLabel',background=SURFACE,font=('Segoe UI',13,'bold'))
        style.configure('TButton',padding=(13,9),background='#e7edf0',borderwidth=0,focuscolor=ACCENT)
        style.map('TButton',background=[('active','#dce5e9')],foreground=[('disabled','#9aa8af')])
        style.configure('Primary.TButton',background=ACCENT,foreground='white',font=('Segoe UI',10,'bold'))
        style.map('Primary.TButton',background=[('disabled','#c2d2cf'),('active','#09675d')],foreground=[('disabled','#637e78'),('active','white')])
        style.configure('TEntry',fieldbackground=SURFACE,padding=8,bordercolor='#d9e1e5',lightcolor='#d9e1e5',darkcolor='#d9e1e5')
        style.configure('TCombobox',padding=7,fieldbackground=SURFACE,background='#e7edf0',arrowsize=14)
        style.map('TCombobox',fieldbackground=[('readonly',SURFACE)],foreground=[('readonly',INK)])
        style.configure('TCheckbutton',background=SURFACE,padding=(0,5))
        style.map('TCheckbutton',background=[('active',SURFACE)])
        style.configure('TRadiobutton',background=SURFACE,padding=(0,7))
        style.configure('TLabelframe',background=BG,bordercolor='#d9e1e5')
        style.configure('Treeview',background=SURFACE,fieldbackground=SURFACE,rowheight=34,borderwidth=0)
        style.configure('Treeview.Heading',background='#e7edf0',padding=9,font=('Segoe UI',10,'bold'))
        style.configure('Games.Treeview',rowheight=62,font=('Segoe UI',12),indent=10)
        style.map('Treeview',background=[('selected','#cfece5')],foreground=[('selected',INK)])
        style.configure('Horizontal.TProgressbar',background=ACCENT,troughcolor='#e7edf0',borderwidth=0,thickness=6)
        style.layout('Pages.TNotebook.Tab',[])
        style.configure('Pages.TNotebook',background=BG,borderwidth=0,tabmargins=0)

    def button(self,parent,text,command,primary=False):
        button=ttk.Button(parent,text=text,command=command,style='Primary.TButton' if primary else 'TButton')
        if command!=self.show_log:self.action_buttons.append(button)
        return button

    def heading(self,page,title,description):
        ttk.Label(page,text=title,style='Title.TLabel',wraplength=850).pack(anchor='w')
        ttk.Label(page,text=description,style='Muted.TLabel',wraplength=800).pack(anchor='w',pady=(5,12))
        page.bind('<Configure>',lambda e:self.wrap_labels(page,e.width-40),add='+')

    def wrap_labels(self,parent,width):
        for widget in parent.winfo_children():
            if isinstance(widget,ttk.Label) and int(widget.cget('wraplength') or 0):
                widget.configure(wraplength=max(150,width))

    def responsive_cards(self,columns,left,right):
        def layout(event):
            stacked=event.width<790
            columns.columnconfigure(0,weight=1,uniform='' if stacked else 'cards')
            columns.columnconfigure(1,weight=0 if stacked else 1,uniform='' if stacked else 'cards')
            left.grid_configure(row=0,column=0,padx=(0,0 if stacked else 8),pady=(0,12 if stacked else 0))
            right.grid_configure(row=1 if stacked else 0,column=0 if stacked else 1,padx=(0 if stacked else 8,0))
        columns.bind('<Configure>',layout)
        for card in (left,right):
            card.bind('<Configure>',lambda e,c=card:self.wrap_labels(c,e.width-28))

    def card(self,parent,title):
        card=ttk.Frame(parent,style='Card.TFrame',padding=14)
        ttk.Label(card,text=title,style='CardTitle.TLabel').pack(anchor='w',pady=(0,9))
        return card

    def field(self,parent,name,label,show=None):
        ttk.Label(parent,text=label,style='CardMuted.TLabel').pack(anchor='w',pady=(6,4))
        entry=ttk.Entry(parent,textvariable=self.vars[name],show=show or '')
        entry.pack(fill='x');return entry

    def setup_page(self):
        page=self.pages[0]
        self.heading(page,'Two headsets. One library.','Connect Frame over your network, then plug Quest into your PC to choose your games.')
        columns=ttk.Frame(page);columns.pack(fill='x');columns.columnconfigure((0,1),weight=1,uniform='cards')
        frame=self.card(columns,'01  /  Steam Frame');frame.grid(row=0,column=0,sticky='nsew',padx=(0,8))
        self.field(frame,'host','Hostname or IP address')
        row=ttk.Frame(frame,style='Card.TFrame');row.pack(fill='x',pady=(3,0))
        left=ttk.Frame(row,style='Card.TFrame');left.pack(side='left',fill='x',expand=True,padx=(0,10))
        right=ttk.Frame(row,style='Card.TFrame',width=85);right.pack(side='right');right.pack_propagate(False);right.configure(height=65)
        self.field(left,'username','SSH username');self.field(right,'port','Port')
        self.field(frame,'password','SSH password · never saved',show='•')
        self.button(frame,'Verify Frame connection',self.test_ssh,True).pack(fill='x',pady=(16,8))
        ttk.Label(frame,text='First connection requires Frame USB to verify its identity. After that, use Wi-Fi.',style='CardMuted.TLabel',wraplength=350).pack(anchor='w')
        quest=self.card(columns,'02  /  Meta Quest');quest.grid(row=0,column=1,sticky='nsew',padx=(8,0))
        for title,body in [('Enable developer mode','Enable USB debugging on Quest and development access on Frame.'),('Connect Quest by USB','Unlock your Quest and accept the USB debugging prompt.'),('Keep Frame on your network','Quest stays plugged into your PC. Frame can install over Wi-Fi using SSH.')]:
            ttk.Label(quest,text=title,style='Card.TLabel',font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(8,4))
            ttk.Label(quest,text=body,style='CardMuted.TLabel',wraplength=340).pack(anchor='w',pady=(0,9))
        self.button(quest,'Browse Quest games  →',self.browse_quest,True).pack(fill='x',pady=(13,0))
        self.responsive_cards(columns,frame,quest)
        self.button(page,'Already converted? Open a saved build…',self.open_build).pack(anchor='w',pady=(14,0))

    def browse_quest(self):
        self.tabs.select(1)
        if not self.package_list:self.scan()

    def quest_page(self):
        page=self.pages[1]
        self.heading(page,'Choose what comes with you.','Select one game to customize, or several to build a transfer queue.')
        tools=ttk.Frame(page);tools.pack(fill='x',pady=(0,10))
        ttk.Label(tools,text='Search',style='Muted.TLabel').pack(side='left',padx=(0,10))
        ttk.Entry(tools,textvariable=self.vars['filter']).pack(side='left',fill='x',expand=True,padx=(0,10))
        self.button(tools,'Scan Quest',self.scan).pack(side='right')
        self.vars['filter'].trace_add('write',lambda *_:self.filter_packages())
        ttk.Label(page,textvariable=self.library_count,style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        well=ttk.Frame(page,style='Card.TFrame');well.pack(fill='both',expand=True)
        self.listbox=GameList(well)
        scroll=ttk.Scrollbar(well,command=self.listbox.yview);scroll.pack(side='right',fill='y')
        self.listbox.configure(yscrollcommand=scroll.set);self.listbox.pack(fill='both',expand=True,ipady=8)
        self.listbox.bind('<<TreeviewSelect>>',self.library_selection_changed)
        self.listbox.bind('<Double-Button-1>',lambda _:self.select_game())
        self.listbox.bind('<Button-3>',self.game_details_menu)
        self.listbox.bind('<Shift-F10>',self.game_details_menu)
        ttk.Label(page,text='Ctrl / Shift-click for multiple games. Right-click for package details. Searching clears selection.',style='Muted.TLabel').pack(anchor='w',pady=(9,10))
        actions=ttk.Frame(page);actions.pack(fill='x')
        self.button(actions,'Select all',self.select_all).pack(side='left')
        ttk.Label(actions,textvariable=self.selection_count,style='Muted.TLabel').pack(side='left',padx=15)
        self.button(actions,'Review selection  →',self.select_game,True).pack(side='right')

    def select_all(self):
        self.listbox.selection_set(0,'end');self.library_selection_changed()

    def game_details_menu(self,event):
        if getattr(event,'num',None)==3:
            row=self.listbox.identify_row(event.y)
            if not row:return
            self.listbox.selection_set(self.listbox.index(row))
        selected=self.listbox.curselection()
        if not selected:return
        package=self.visible_packages[selected[0]]
        menu=tk.Menu(self,tearoff=False)
        def copy():self.clipboard_clear();self.clipboard_append(package)
        menu.add_command(label='Copy package ID',command=copy)
        menu.add_command(label='App details…',command=lambda:messagebox.showinfo('App details',self.package_names.get(package,package)+'\n\n'+package,parent=self))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release();menu.destroy()

    def library_selection_changed(self,event=None):
        count=len(self.listbox.curselection())
        self.selection_count.set(f'{count} game'+('s' if count!=1 else '')+' selected')

    def convert_page(self):
        page=self.pages[2]
        self.heading(page,'Make it feel at home.','Review your game and performance preferences before creating a Frame-ready copy.')
        self.selected=tk.StringVar(value='No game selected · choose a Quest game or open a saved build.')
        ttk.Label(page,textvariable=self.selected,style='Muted.TLabel',wraplength=830).pack(anchor='w',pady=(0,6))
        columns=ttk.Frame(page);columns.pack(fill='x');columns.columnconfigure((0,1),weight=1,uniform='cards')
        game=self.card(columns,'Library details');game.grid(row=0,column=0,sticky='nsew',padx=(0,8))
        self.field(game,'title','Game title')
        self.field(game,'steam_id','Steam App ID · optional artwork')
        self.button(game,'Find artwork',self.search_artwork).pack(anchor='w',pady=9)
        self.art_matches=ttk.Combobox(game,state='readonly',width=22);self.art_matches.pack(fill='x')
        self.art_matches.set('Choose an artwork match')
        self.art_matches.bind('<<ComboboxSelected>>',self.choose_artwork)
        ttk.Checkbutton(game,text='Search Steam automatically',variable=self.auto_art).pack(anchor='w',pady=(8,0))
        ttk.Label(game,text='Search sends the game title to Steam. Confirm the matching edition; artwork is optional.',style='CardMuted.TLabel',wraplength=345).pack(anchor='w',pady=(5,0))
        performance=self.card(columns,'Performance & compatibility');performance.grid(row=0,column=1,sticky='nsew',padx=(8,0))
        for name,title,values in [('scale','Resolution scale (%)',('50','67','75','85','100','125','150','175','200')),('refresh_rate','Headset refresh rate (Hz)',('Default','72','80','90','120','144')),('headset','Reported headset (experimental; rebuild required)',('Default','Quest 2','Quest 3'))]:
            ttk.Label(performance,text=title,style='CardMuted.TLabel').pack(anchor='w',pady=(6,4))
            ttk.Combobox(performance,textvariable=self.vars[name],values=values,state='readonly',width=15).pack(fill='x')
        ttk.Label(performance,text='100% uses the runtime recommendation. Refresh rate is a request, not an FPS cap.',style='CardMuted.TLabel',wraplength=350).pack(anchor='w',pady=5)
        ttk.Checkbutton(performance,text='Physical-controller compatibility',variable=self.controllers).pack(anchor='w')
        ttk.Checkbutton(performance,text='Disable unsupported Quest foveation',variable=self.foveation).pack(anchor='w')
        ttk.Checkbutton(performance,text='Fix black screen (swapchain rectangles; rebuild)',variable=self.rect_fix).pack(anchor='w')
        ttk.Checkbutton(performance,text='Open-source OpenXR dispatcher (experimental; rebuild)',variable=self.open_dispatcher).pack(anchor='w')
        ttk.Label(performance,text='Replaces OVR Port’s closed dispatcher with openxr-dispatcher-open. Logs gaps under the OpenXRDispatch logcat tag.',style='CardMuted.TLabel',wraplength=350).pack(anchor='w',pady=(0,5))
        self.responsive_cards(columns,game,performance)
        actions=ttk.Frame(page);actions.pack(fill='x',pady=(16,0))
        self.button(actions,'Open saved build…',self.open_build).pack(side='left')
        self.button(actions,'Rebuild APK only',self.rebuild_apk).pack(side='left',padx=8)
        self.button(actions,'Go to installation  →',lambda:self.tabs.select(3)).pack(side='right',padx=(10,0))
        self.button(actions,'Back up & convert',self.convert,True).pack(side='right')

    def install_page(self):
        page=self.pages[3]
        self.heading(page,'Next stop: your Frame.','Install your converted game, then launch it directly from the Steam library.')
        self.ready=tk.StringVar(value='No build loaded. Convert a Quest game or open a saved build first.')
        ttk.Label(page,textvariable=self.ready,style='Muted.TLabel',wraplength=830).pack(anchor='w',pady=(0,14))
        card=self.card(page,'Transfer & destination');card.pack(fill='x')
        modes=ttk.Frame(card,style='Card.TFrame');modes.pack(fill='x')
        ttk.Radiobutton(modes,text='Wi-Fi · SSH / SFTP',variable=self.wireless_install,value=True).pack(side='left',padx=(0,30))
        ttk.Radiobutton(modes,text='USB · ADB + SSH',variable=self.wireless_install,value=False).pack(side='left')
        ttk.Label(card,text='Both modes use your Frame connection details. Wi-Fi needs the same network; USB needs Frame connected to your PC.',style='CardMuted.TLabel',wraplength=800).pack(anchor='w',pady=(5,12))
        self.storage_picker=StoragePicker(card,self);self.storage_picker.pack(fill='x',pady=(0,10))
        ttk.Checkbutton(card,text='Add to Steam with artwork · briefly restarts Steam',variable=self.add_steam).pack(anchor='w')
        ttk.Label(card,text='Keep Frame awake during Wi-Fi transfers. Quest2Frame does not change the device’s sleep or power settings.',style='CardMuted.TLabel',wraplength=790).pack(anchor='w',pady=(10,0))
        actions=ttk.Frame(page);actions.pack(fill='x',pady=14)
        self.button(actions,'Open saved build…',self.open_build).pack(side='left')
        self.button(actions,'Install on Frame',self.install,True).pack(side='right')
        maintenance=ttk.Frame(page);maintenance.pack(fill='x')
        self.button(maintenance,'Apply settings only',self.update_settings).pack(side='left',padx=(0,8))
        self.button(maintenance,'Repair Steam shortcut',self.repair_shortcut).pack(side='left')
        self.button(maintenance,'Install APK only',self.install_apk_only).pack(side='right')
        ttk.Label(page,text='Existing saves are preserved. Updates stay on the original storage device. Compatibility varies by game.',style='Muted.TLabel',wraplength=820).pack(anchor='w',pady=(13,0))

    def navigation_changed(self,event=None):
        index=self.tabs.index(self.tabs.select())
        for i,button in enumerate(self.nav):button.configure(bg='#294b59' if i==index else NAV,fg='white' if i==index else '#bdccd5')

    def page_wheel(self,event):
        widget=event.widget
        while widget is not None:
            if isinstance(widget,ScrollPage):widget.wheel(event);break
            widget=getattr(widget,'master',None)

    def lock_actions(self,locked):
        if locked:
            self._locked=[]
            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child,(ttk.Entry,ttk.Combobox,ttk.Checkbutton,ttk.Radiobutton,tk.Listbox)):
                        self._locked.append((child,str(child.cget('state'))));child.configure(state='disabled')
                    walk(child)
            walk(self.tabs)
            for button in self.action_buttons:
                if button.winfo_exists():button.configure(state='disabled')
        else:
            for widget,state in self._locked:
                if widget.winfo_exists():widget.configure(state=state)
            self._locked=[]
            for button in self.action_buttons:
                if button.winfo_exists():button.configure(state='normal')

    def show_log(self):
        self.log_window.deiconify();self.log_window.lift()

    def about(self):
        messagebox.showinfo('Quest2Frame · About & licenses','Quest2Frame — native desktop edition\n\nOriginal source: GPL-3.0-only, without warranty. Dependencies, game content and artwork retain their own licenses.\n\n'+str(ROOT/'LICENSE')+'\n'+str(ROOT/'THIRD_PARTY_NOTICES.md'),parent=self)
