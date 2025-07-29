import speech_recognition as sr
from gtts import gTTS
from pydub import AudioSegment
from pydub.playback import play
import io
import os

# Function to convert text to speech and play it without saving a file
def jarvis_speak(text):
    try:
        # Convert text to speech using gTTS
        tts = gTTS(text=text, lang='en')
        
        # Create an in-memory byte stream
        audio_fp = io.BytesIO()
        
        # Write the gTTS output to the byte stream
        tts.write_to_fp(audio_fp)
        audio_fp.seek(0)  # Reset stream position

        # Load the byte stream as an audio segment
        song = AudioSegment.from_file(audio_fp, format="mp3")

        # Play the audio segment
        play(song)

    except Exception as e:
        print(f"Error in jarvis_speak: {e}")

# Function to listen to voice commands using the microphone
def listen_command():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening for your command...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)

        try:
            command = recognizer.recognize_google(audio)
            print(f"You said: {command}")
            return command.lower()
        except sr.UnknownValueError:
            jarvis_speak("Sorry, I didn't catch that. Could you please repeat?")
            return ""
        except sr.RequestError as e:
            jarvis_speak("Sorry, my speech service is down.")
            return ""

# Function to open applications based on voice commands
def open_application(app_name):
    apps = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",  # Adjust as necessary
    }

    if app_name in apps:
        jarvis_speak(f"Opening {app_name}.")
        os.startfile(apps[app_name])  # Use os.startfile to open apps
    else:
        jarvis_speak(f"Sorry, I don't know how to open {app_name}.")

# Main loop that listens for voice commands and opens apps
if __name__ == "__main__":
    jarvis_speak("Hello, I am Jarvis. What would you like me to open?")

    while True:
        command = listen_command()

        if command:
            if "open" in command:
                app_name = command.replace("open", "").strip()
                open_application(app_name)

            elif "exit" in command or "goodbye" in command:
                jarvis_speak("Goodbye, master!")
                break

            else:
                jarvis_speak("I didn't understand that command.")
