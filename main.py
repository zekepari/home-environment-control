import os
import dropbox
from gpiozero import Button, LED
from flask import Flask, Response, render_template
import adafruit_dht
from gpiozero import DistanceSensor, LED
from board import D4
from time import time, sleep
import json

app = Flask(__name__)

# Initialize Dropbox client with access token
DROPBOX_ACCESS_TOKEN = os.getenv('DROPBOX_ACCESS_TOKEN')
dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

# Initialize sensors
dht_sensor = adafruit_dht.DHT11(D4)
distance_sensor = DistanceSensor(echo=24, trigger=18)

# Initialize LEDs
green_led = LED(17)
yellow_led = LED(27)
white_led = LED(22)  # New white LED for room light

# Initialize buttons
button = Button(23)  # Button to control the white LED
upload_button = Button(21)  # Button to upload data to Dropbox

MOVEMENT_THRESHOLD = 0.1  # Threshold for detecting movement
NO_MOVEMENT_LIMIT = 5  # Number of readings to assume no movement (for entry/exit logic)
COOLDOWN_PERIOD = 10  # Cooldown period (in seconds) to prevent false re-entry detection

# State tracking
movement_detected = 0  # Count of consecutive detections with no significant change
in_room = False  # Assume nobody is in the room initially
previous_distance = None  # To store the previous distance value
movement_after_no_movement = False  # To track if we detect movement after no movement period
last_exit_time = None  # Timestamp of the last exit to enforce cooldown

# Function to categorize temperature and humidity
def categorize_conditions(temp, humidity):
    temp_category = 'moderate' if 18 <= temp <= 24 else 'cold' if temp < 18 else 'hot'
    humidity_category = 'moderate' if 30 <= humidity <= 60 else 'low' if humidity < 30 else 'high'
    return temp_category, humidity_category

# Function to toggle the white LED manually (button override)
def toggle_white_led():
    white_led.toggle()

# Set up button to control the white LED
button.when_pressed = toggle_white_led

# Function to retrieve sensor data dynamically
def get_sensor_data():
    global previous_distance, movement_detected, in_room, movement_after_no_movement, last_exit_time
    try:
        # Read distance sensor data
        dist = distance_sensor.distance
        if previous_distance is not None and abs(dist - previous_distance) > MOVEMENT_THRESHOLD:
            green_led.on()  # Movement detected
            yellow_led.off()

            # Check if we're in the cooldown period
            if last_exit_time and time() - last_exit_time < COOLDOWN_PERIOD:
                print("Cooldown active. Ignoring movement.")
            else:
                if not in_room:
                    in_room = True  # Someone has entered the room
                    print("Someone has entered the room.")
                    white_led.on()  # Turn on LED when someone is in the room

                elif movement_after_no_movement:
                    in_room = False  # Someone has left the room
                    last_exit_time = time()  # Set the time of exit
                    movement_after_no_movement = False
                    print("Someone has left the room.")
                    white_led.off()  # Turn off LED when the room is empty

            movement_detected = 0  # Reset movement detection counter since there is movement

        else:
            yellow_led.on()  # No significant movement
            green_led.off()

            movement_detected += 1

            # If no movement is detected for 5 cycles and someone is in the room
            if movement_detected >= NO_MOVEMENT_LIMIT and in_room:
                movement_after_no_movement = True  # Prepare to detect the next movement as exit
                print("Waiting for next movement to assume exit...")

        previous_distance = dist  # Update the previous distance with the current one

        # Read DHT sensor data
        temperature = dht_sensor.temperature
        humidity = dht_sensor.humidity

        temp_category, humidity_category = categorize_conditions(temperature, humidity)

        warnings = []
        if temp_category in ['hot', 'cold']:
            warnings.append(f"Warning: The room is too {temp_category}!")
        if humidity_category in ['high', 'low']:
            warnings.append(f"Warning: The humidity is too {humidity_category}!")

        # Return the latest sensor data
        return {
            "distance": dist * 100,  # convert to cm
            "temperature": temperature,
            "humidity": humidity,
            "temp_category": temp_category,
            "humidity_category": humidity_category,
            "warnings": warnings,
            "in_room": in_room,
            "white_led_status": white_led.is_lit
        }

    except RuntimeError as error:
        print(f"Error reading from sensors: {error}")
        return {}
# Function to upload data to Dropbox
def upload_to_dropbox(data):
    file_name = f"sensor_data_{int(time())}.json"
    try:
        dbx.files_upload(json.dumps(data).encode(), f'/{file_name}')
        print(f"Uploaded {file_name} to Dropbox successfully.")
    except dropbox.exceptions.ApiError as err:
        print(f"Failed to upload {file_name} to Dropbox: {err}")

# Set up button to upload data to Dropbox
upload_button.when_pressed = lambda: upload_to_dropbox(get_sensor_data())

# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

def format_sse(data):
    return f"data: {data}\n\n"

@app.route('/events')
def events():
    def event_stream():
        with app.app_context():  # Ensuring this runs within the Flask app context
            while True:
                sensor_data = get_sensor_data()  # Fetch sensor data
                if sensor_data:
                    yield format_sse(json.dumps(sensor_data))  # Send JSON data as SSE
                sleep(1)  # Sleep before sending the next update

    return Response(event_stream(), content_type='text/event-stream')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
