import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os

def start_server():
    # 获取输入框的值
    port = port_var.get()
    name = name_var.get()
    pwd = pwd_var.get()
    max_file = max_file_var.get()

    # 拼接启动命令
    cmd = [sys.executable, "server.py", "--port", str(port), "--name", name, "--max-file", str(max_file)]
    if pwd.strip():
        cmd.extend(["--password", pwd.strip()])

    try:
        # 启动 server.py
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        root.destroy()  # 启动成功后，自动关闭这个小启动器
    except Exception as e:
        messagebox.showerror("启动失败", f"无法启动服务器：\n{e}")

# 创建主窗口
root = tk.Tk()
root.title("Lanyard 启动器")
root.geometry("320x240")
root.resizable(False, False)

# 设置变量默认值
port_var = tk.IntVar(value=8443)
name_var = tk.StringVar(value="LANYARD")
pwd_var = tk.StringVar(value="")
max_file_var = tk.IntVar(value=500)

# 界面布局
frame = ttk.Frame(root, padding=20)
frame.pack(fill=tk.BOTH, expand=True)

ttk.Label(frame, text="房间名称:").grid(row=0, column=0, sticky=tk.W, pady=5)
ttk.Entry(frame, textvariable=name_var).grid(row=0, column=1, pady=5, sticky=tk.EW)

ttk.Label(frame, text="监听端口:").grid(row=1, column=0, sticky=tk.W, pady=5)
ttk.Entry(frame, textvariable=port_var).grid(row=1, column=1, pady=5, sticky=tk.EW)

ttk.Label(frame, text="房间密码:").grid(row=2, column=0, sticky=tk.W, pady=5)
ttk.Entry(frame, textvariable=pwd_var).grid(row=2, column=1, pady=5, sticky=tk.EW)
ttk.Label(frame, text="(留空表示不加密)", foreground="gray", font=("", 8)).grid(row=3, column=1, sticky=tk.W)

ttk.Label(frame, text="最大文件(MB):").grid(row=4, column=0, sticky=tk.W, pady=5)
ttk.Entry(frame, textvariable=max_file_var).grid(row=4, column=1, pady=5, sticky=tk.EW)

btn_start = ttk.Button(frame, text="🚀 启动服务器", command=start_server)
btn_start.grid(row=5, columnspan=2, pady=15)

# 让第二列自动拉伸
frame.columnconfigure(1, weight=1)

root.mainloop()