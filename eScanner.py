import time
import subprocess
import pyautogui

def right_arrow_in_safari():
    # Sender højre pil til Safari via AppleScript (key code 124 = Right Arrow)
    script = '''
    tell application "Safari" to activate
    delay 0.05
    tell application "System Events" to key code 124
    '''
    subprocess.run(["osascript", "-e", script], check=False)

def ensure_page_focus():
    # Klik i midten af skærmen, så fokus flyttes fra adressefeltet til selve siden
    w, h = pyautogui.size()
    pyautogui.click(w//2, int(h*0.6))  # lidt under midten hjælper tit på menulinjer

def main():
    try:
        n = int(input("Hvor mange screenshots? "))
        delay = float(input("Sekunder mellem hvert (fx 1.0): ") or "1.0")
    except ValueError:
        print("Ugyldigt input. Brug tal.")
        return

    print("Du har 5 sekunder til at bringe Safari-fanen frem...")
    time.sleep(5)

    for i in range(n):
        # Sørg for at siden har fokus
        #ensure_page_focus()
        #time.sleep(0.05)

        # Tag screenshot
        img = pyautogui.screenshot()
        img.save(f"screenshot_{i+1+150:03d}.png")

        # Bladr til næste (pil til højre)
        right_arrow_in_safari()

        time.sleep(delay)

    print("Færdig!")

if __name__ == "__main__":
    main()