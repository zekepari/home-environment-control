from gpiozero import Button, LED
from flask import Flask, Response, render_template, jsonify
import adafruit_dht
from gpiozero import DistanceSensor, LED
from board import D4
from time import time, sleep

app = Flask(__name__)

# Initialize sensors
dht_sensor = adafruit_dht.DHT11(D4)
distance_sensor = DistanceSensor(echo=24, trigger=18)

# Initialize LEDs
green_led = LED(17)
yellow_led = LED(27)
white_led = LED(22)  # New white LED for room light

# Initialize button
button = Button(23)  # Button to control the white LED

MOVEMENT_THRESHOLD = 0.1  # Threshold for detecting movement
NO_MOVEMENT_LIMIT = 5  # Number of readings to assume no movement (for entry/exit logic)
COOLDOWN_PERIOD = 10  # Cooldown period (in seconds) to prevent false re-entry detection

# State tracking
movement_detected = 0  # Count of consecutive detections with no significant change
in_room = False  # Assume nobody is in the room initially
previous_distance = None  # To store the previous distance value
movement_after_no_movement = False  # To track if we detect movement after no movement period
last_exit_time = None  # Timestamp of the last exit to enforce cooldown
led_overridden = False  # Tracks whether the button has overridden the auto LED behavior

# Function to categorize temperature
def categorize_temperature(temp):
    if temp < 18:
        return 'cold'
    elif 18 <= temp <= 24:
        return 'moderate'
    else:
        return 'hot'

# Function to categorize humidity
def categorize_humidity(humidity):
    if humidity < 30:
        return 'low'
    elif 30 <= humidity <= 60:
        return 'moderate'
    else:
        return 'high'

# Function to automatically control the white LED based on room occupancy
def auto_control_led():
    global led_overridden
    if in_room and not led_overridden:
        white_led.on()  # Turn on LED when someone is in the room
    elif not in_room and not led_overridden:
        white_led.off()  # Turn off LED when the room is empty

# Function to toggle the white LED manually (button override)
def toggle_white_led():
    global led_overridden
    white_led.toggle()  # Reverse the current LED state
    led_overridden = not led_overridden  # Toggle the override flag

# Set up button to control the white LED
button.when_pressed = toggle_white_led

# Function to retrieve sensor data dynamically
def get_sensor_data():
    global previous_distance, movement_detected, in_room, movement_after_no_movement, last_exit_time
    try:
        # Read distance sensor data
        dist = distance_sensor.distance
        distance_change = None
        if previous_distance is not None:
            distance_change = abs(dist - previous_distance)

        current_time = time()  # Get the current timestamp

        # If there's significant movement
        if distance_change is not None and distance_change > MOVEMENT_THRESHOLD:
            green_led.on()  # Movement detected
            yellow_led.off()

            # Check if we're in the cooldown period
            if last_exit_time and (current_time - last_exit_time < COOLDOWN_PERIOD):
                print("Cooldown active. Ignoring movement.")
            else:
                # If not in the room and no cooldown is active, assume entry
                if not in_room:
                    in_room = True
                    print("Someone has entered the room.")
                    led_overridden = False  # Reset override when auto behavior changes
                    auto_control_led()

                elif movement_after_no_movement:
                    # Movement detected after no movement period, assume exit
                    in_room = False
                    last_exit_time = current_time  # Set the time of exit
                    movement_after_no_movement = False
                    print("Someone has left the room.")
                    led_overridden = False  # Reset override when auto behavior changes
                    auto_control_led()

            # Reset movement detection counter since there is movement
            movement_detected = 0

        else:
            yellow_led.on()  # No significant movement
            green_led.off()

            movement_detected += 1

            # If no movement is detected for 5 cycles and someone is in the room
            if movement_detected >= NO_MOVEMENT_LIMIT and in_room:
                # Prepare to detect the next movement as exit
                movement_after_no_movement = True
                print("Waiting for next movement to assume exit...")

        previous_distance = dist  # Update the previous distance with the current one

        # Read DHT sensor data
        temperature = dht_sensor.temperature
        humidity = dht_sensor.humidity

        temp_category = categorize_temperature(temperature)
        humidity_category = categorize_humidity(humidity)

        warnings = []

        if temp_category == 'hot':
            warnings.append("Warning: The room is too hot!")
        elif temp_category == 'cold':
            warnings.append("Warning: The room is too cold!")

        if humidity_category == 'high':
            warnings.append("Warning: The humidity is too high!")
        elif humidity_category == 'low':
            warnings.append("Warning: The humidity is too low!")

        # Return the latest sensor data
        sensor_data = {
            "distance": dist * 100,  # convert to cm
            "temperature": temperature,
            "humidity": humidity,
            "temp_category": temp_category,
            "humidity_category": humidity_category,
            "warnings": warnings,
            "in_room": in_room,
            "white_led_status": white_led.is_lit
        }

        return sensor_data

    except RuntimeError as error:
        print(f"Error reading from sensors: {error}")
        return {}

# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

def format_sse(data):
    return f"data: {data}\n\n"

@app.route('/events')
def events():
    def event_stream():
        while True:
            sensor_data = get_sensor_data()
            if sensor_data:
                yield format_sse(jsonify(sensor_data).get_data(as_text=True))  # Send data as SSE
            sleep(1)

    return Response(event_stream(), content_type='text/event-stream')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
