// DroidToolbox.exe：绿色版启动器。用同目录 runtime\pythonw.exe 运行 droidtoolbox\main.py，参数原样传递（例如 --mock）。
// 由 packaging/build_release.py 调用 .NET Framework 自带的 csc.exe 编译，不需要安装 SDK。
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Android 系统工程师工具箱")]
[assembly: System.Reflection.AssemblyProduct("DroidToolbox")]
[assembly: System.Reflection.AssemblyVersion("@VERSION@.0")]
[assembly: System.Reflection.AssemblyFileVersion("@VERSION@.0")]

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, "runtime", "pythonw.exe");
        string main = Path.Combine(dir, "droidtoolbox", "main.py");
        if (!File.Exists(py) || !File.Exists(main))
        {
            MessageBox.Show("找不到运行环境：\n" + py + "\n" + main + "\n\n请完整解压整个文件夹后再运行。",
                            "DroidToolbox", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var cmd = new StringBuilder(Quote(main));
        foreach (string a in args)
            cmd.Append(' ').Append(Quote(a));
        try
        {
            var psi = new ProcessStartInfo(py, cmd.ToString()) { UseShellExecute = false, WorkingDirectory = dir };
            Process.Start(psi);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("启动失败：" + e.Message, "DroidToolbox", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    static string Quote(string s)
    {
        if (s.Length > 0 && s.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            return s;
        var b = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in s)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { b.Append('\\', slashes * 2 + 1); slashes = 0; b.Append('"'); continue; }
            b.Append('\\', slashes); slashes = 0; b.Append(c);
        }
        b.Append('\\', slashes * 2).Append('"');
        return b.ToString();
    }
}
