import subprocess
import sys

def clear_cache():
    print("Clearing cache for all apps on connected Android device...")
    try:
        result = subprocess.run([
            "adb", "shell", "pm", "trim-caches", "1K"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        if result.returncode == 0:
            print("[SUCCESS] Cache cleared for all apps.")
            if result.stdout.strip():
                print(result.stdout.strip())
        else:
            print("[ERROR] Failed to clear cache.")
            print(result.stderr.strip())
    except FileNotFoundError:
        print("[ERROR] adb not found. Please install Android Platform Tools and add adb to your PATH.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("[ERROR] adb command timed out.")
        sys.exit(1)

if __name__ == "__main__":
    clear_cache() 