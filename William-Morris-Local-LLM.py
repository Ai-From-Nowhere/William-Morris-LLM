import threading
import queue
from ollama import ChatResponse
from ollama import chat
from pynput import keyboard
import os
import time
from lib import epd7in5_V2
from signal import pause
from gpiozero import RotaryEncoder
from PIL import Image, ImageDraw, ImageFont
from enum import Enum, auto
import logging
import re

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class EventType(Enum):
    LLM_RESPONSE = auto()
    DISPLAY_UPDATE = auto()
    SHUTDOWN = auto()
    KEYBOARDINPUT = auto()
    BAR_UPDATE = auto()
    BAR_FULL = auto()
    INPUTSENT = auto()

# Event class to standardize our events
class Event:
    def __init__(self, event_type, data=None):
        self.type = event_type
        self.data = data
        self.timestamp = time.time()

class DeviceController:
    def __init__(self):
        logging.info("Initializing DeviceController...")
        
        # Initialize shared event queue
        self.event_queue = queue.Queue()

        # Initialize components
        self.rotary = RotaryEncoderClass(self.event_queue)
        self.display = EpaperDisplay(self.event_queue)
        self.llm = LocalLLM(self.event_queue)
        
        # Shared state
        self.current_prompt = ""
        self.user_input = ""
        self.current_response = ""
        self.loading_progress = -1
        self.is_running = True
        # self.llm_output_ready = False 
        self.loading_bar_full = False 
        
        # Lock for thread-safe access to shared state
        self.state_lock = threading.Lock()
        
        logging.info("DeviceController initialized successfully.")
    
    def start(self):
        """Start all component threads and the main event loop"""
        logging.info("Starting DeviceController threads...")
        
        # Start component threads
        display_thread = threading.Thread(target=self.display.run)
        rotary_thread = threading.Thread(target=self.rotary.run)
        llm_thread = threading.Thread(target=self.llm.run)
        
        display_thread.daemon = True
        rotary_thread.daemon = True
        llm_thread.daemon = True
        
        display_thread.start()
        rotary_thread.start()
        llm_thread.start()
        
        logging.info("All threads started. Entering event processing loop.")
        
        # Start main event processing loop
        self.process_events()
        
    def process_events(self):
        """Main event loop"""
        logging.info("Processing events...")
        try:
            while self.is_running:
                try:
                    # Get event with timeout to allow for graceful shutdown
                    event = self.event_queue.get(timeout=0.1)
                    logging.debug(f"Event received: {event.type}")
                    self.handle_event(event)
                    self.event_queue.task_done()
                except queue.Empty:
                    continue
        except KeyboardInterrupt:
            self.shutdown()
    
    def handle_event(self, event):
        """Handle events based on their type"""
        logging.debug(f"Handling event: {event.type}")
        
        if event.type == EventType.SHUTDOWN:
            logging.info("Shutdown event received.")
            self.shutdown()

        elif event.type == EventType.KEYBOARDINPUT:
            logging.info("Keyboard input event detected.")
            self.display.prtext(self.llm.user_input)

        elif event.type == EventType.INPUTSENT:
            logging.info("User input sent. Updating display and loading bar.")
            self.display.loadingbar()
            self.display.display_user_input(self.llm.user_input)
            self.rotary.is_running = True

        elif event.type == EventType.BAR_UPDATE:
            logging.info("Loading bar update event detected.")
            self.display.update_loading_bar(self.rotary.loading_progress)

        elif event.type == EventType.BAR_FULL:
            logging.info("Loading bar full event received.")
            self.loading_bar_full = True  
            self.check_display_response()  

        elif event.type == EventType.LLM_RESPONSE:
            logging.info("LLM Response receieved: {self.current_response}")
            # self.current_response = self.llm.response #Store response
            self.check_display_response()

    def check_display_response(self):
        #"""Check if both conditions (bar full & LLM response ready) are met before displaying"""
        if self.llm.llm_output_ready and self.loading_bar_full:
            logging.info("Both LLM response and loading bar are ready. Displaying response.")
            self.display.display_response(self.llm.response.message.content)
            
            self.llm.llm_output_ready = False
            self.loading_bar_full = False

    def shutdown(self):
        """Shutdown the application gracefully"""
        logging.info("Shutting down DeviceController...")
        self.is_running = False
        
        # Notify components to shut down
        self.display.stop()
        self.rotary.stop()
        self.llm.stop()
        
        logging.info("DeviceController shutdown complete.")

# E-paper display component
class EpaperDisplay:

    def __init__(self, event_queue):

        # Event queues
        self.event_queue = event_queue
        self.update_queue = queue.Queue()
        self.is_running = True

        #initialise the e-paper display settings
        self.epd = epd7in5_V2.EPD()
        self.epd.init()
        self.epd.Clear()

        # Screen layout settings
        self.EPD_WIDTH = self.epd.width
        self.EPD_HEIGHT = self.epd.height
        self.margin_x = 20
        self.margin_y = 125
        self.loading_bar_height = 50
        self.padding = 0
        self.x_start = 20  # Starting X position for text
        self.y_start = 50  # Starting Y position for text
        self.num_bars = 6
        self.loading_bar_width = (self.EPD_WIDTH - (2 * self.margin_x)) // self.num_bars

        self.font_path = "/home/pi/llamalocal/lib/Font.ttc"
        self.font24 = ImageFont.truetype(self.font_path, 24)
        self.line_height = 28

        self.key_count = 2
        # self.user_input = ""
        self.input_counter = 0

        # Display settings
        self.image = Image.new('1', (self.EPD_WIDTH, self.EPD_HEIGHT), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.loading_progress = -1  # Progress for loading bar

    def clear_screen(self):
        # Create a blank image with a white background
        self.image = Image.new('1', (self.EPD_WIDTH, self.EPD_HEIGHT), 255)
        self.draw = ImageDraw.Draw(self.image)

    def clear_area(self, draw, x, y, width, height):
        draw.rectangle((x, y, x + width, y + height), fill=255)
    
    def wrap_text(self, text, font, draw, max_width):
        text = ' '.join(text.split())  # Remove all line breaks and extra spaces
        lines = []
        words = text.split(' ')  # Split into words
        current_line = []

        for word in words:
            test_line = ' '.join(current_line + [word])
            test_width = draw.textbbox((0, 0), test_line, font=font)[2]

            if test_width <= max_width:
                current_line.append(word)
            else:
                lines.append(' '.join(current_line))
                current_line = [word]  # Start a new line

        if current_line:
            lines.append(' '.join(current_line))

        return lines  # Returns a list of properly wrapped lines

    def get_line_height(self, text, font, draw):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1] + 8  # Further increased spacing for better readability

    def loadingbar(self):
        # Draw initial UI
        self.draw.rectangle((0, 0, self.epd.width, self.epd.height // 2 + 24), fill=255)
        self.epd.display(self.epd.getbuffer(self.image))

        outer_rect = [(self.margin_x, self.margin_y), 
            (self.EPD_WIDTH - self.margin_x, self.margin_y + self.loading_bar_height)]
        self.draw.rectangle(outer_rect, fill=255, outline=0)

        wrapped_info = self.wrap_text("William Morris: The machine before you is wholly self-contained. Set the wheel in motion, and by your own hand provide the power required to generate a response!", 
        self.font24, self.draw, self.epd.width - 40)
        
        y_offset = 10
        for line in wrapped_info:
            self.draw.text((20, y_offset), line, font=self.font24, fill=0)
            y_offset += self.get_line_height(line, self.font24, self.draw)

        self.epd.display(self.epd.getbuffer(self.image))
        time.sleep(2)

    def display_user_input(self, current_input):

        # Calculate dynamic line height
        self.line_height = self.get_line_height("Test", self.font24, self.draw)
        print("the lineheight is: ", self.line_height)

        # Adjust y_offset to move the user input 4 lines up
        user_input_y_offset = self.epd.height - (120 + 4 * self.line_height)
        #self.clear_area(self.draw, 20, user_input_y_offset, self.epd.width - 40, 80)
        self.clear_area(self.draw, 20, user_input_y_offset, self.epd.width - 40, self.epd.height - user_input_y_offset)

        wrapped_input = self.wrap_text(f"You: {current_input}", self.font24, self.draw, self.epd.width - 40)
        y_offset = user_input_y_offset

        for line in wrapped_input:
            self.draw.text((20, y_offset), line, font=self.font24, fill=0)
            y_offset += self.line_height

        self.epd.display(self.epd.getbuffer(self.image))

    def prtext(self, current_input):
        self.epd.init_fast()
        self.display_user_input(current_input)
        self.input_counter = 0

    def extract_content(self, response):
        """Extract only the content from the LLM response, ensuring full capture and proper formatting."""

        # Convert response to string if it's an object
        if not isinstance(response, str):
            response = str(response)

        # Regex to find content inside the `content="..."` section
        match = re.search(r"content=['\"](.*?)(?=['\"], images=|, tool_calls=|\))", response, re.DOTALL)

        if match:
            extracted_text = match.group(1)

            # Replace escape sequences with proper formatting
            extracted_text = extracted_text.replace("\\'", "'")  # Convert escaped single quotes to normal ones
            extracted_text = extracted_text.replace('\\"', '"')  # Convert escaped double quotes to normal ones

            # Remove all newlines and ensure spaces remain between words
            extracted_text = extracted_text.replace("\n", " ")  # Replace ALL newlines with a space
            extracted_text = extracted_text.replace("\n\n", " ")  # Remove double newlines
            extracted_text = re.sub(r'\s+', ' ', extracted_text).strip()  # Ensure no extra spaces

            return extracted_text

        return response  # Return raw response if no match

    def display_response(self, response):
        print("in e-paper display class. response is: ", response)
        
        self.draw.rectangle((0, 0, self.epd.width, self.epd.height // 2), fill=255)
        self.epd.display(self.epd.getbuffer(self.image))

        self.wrapped_reply = self.wrap_text(f"William Morris: {response}", self.font24, self.draw, self.epd.width - 40)
        y_offset = 10
        for line in self.wrapped_reply:
            self.draw.text((20, y_offset), line, font=self.font24, fill=0)
            y_offset += self.line_height

        self.epd.display(self.epd.getbuffer(self.image))
         
    def run(self):
        """Run the display thread"""
        
        while self.is_running:
            try:
                update_text = self.update_queue.get(timeout=0.1)
                self.update_display(update_text)
                self.update_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Display error: {e}")

    def queue_update(self, text):
        """Queue a display update from another thread"""
        self.update_queue.put(text)
        
    def stop(self):
        """Stop the display thread"""
        self.is_running = False

    def update_loading_bar(self, loading_progress):
        if loading_progress < self.num_bars:
            current_x = self.margin_x + (loading_progress * self.loading_bar_width)
            self.draw.rectangle([(current_x, self.margin_y), 
                            (current_x + self.loading_bar_width + 3, self.margin_y + self.loading_bar_height)], fill=0)
            
            # Refresh display after every 5 full rotations
            self.epd.init_fast()
            self.epd.display(self.epd.getbuffer(self.image))
            time.sleep(2)

            self.loading_progress += 1  # Move to the next bar

# Rotary encoder component
class RotaryEncoderClass:
    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.is_running = False
        
        # **Rotary Encoder Setup**
        ENCODER_A = 22  # Channel A (Pin 15)
        ENCODER_B = 5   # Channel B (Pin 29)
        self.CPR = 2048  # Counts per full revolution
        self.rotation_count = 0  # Tracks full rotations
        self.loading_progress = -1  # Tracks how many loading bars are filled
        self.max_steps = 0
        self.rotations_met = False

        # Initialize the encoder
        self.encoder = RotaryEncoder(ENCODER_A, ENCODER_B, max_steps = 0)
        self.encoder.when_rotated = self.rotation_detected

        print("Rotate the encoder to advance the loading bar...")

    def run(self):
        while True:
            if self.is_running:
                self.rotation_detected()
            time.sleep(0.05)  # Keeps CPU usage low

    # **Function to Detect a Full Rotation**
    def rotation_detected(self):
        if self.is_running == True:
            if abs(self.encoder.steps) >= self.CPR:
                if self.encoder.steps > 0:
                    self.rotation_count += 1  # Clockwise full turn
                else:
                    self.rotation_count -= 1  # Counterclockwise full turn
                
                self.encoder.steps = 0  # Reset step counter after each full turn
                print(f"Full Rotations: {self.rotation_count}")

                # **Trigger Loading Bar Update Every 5 Rotations**
                if self.rotation_count % 3 == 0:
                    self.rotations_met = True
                    self.loading_progress += 1
                    self.event_queue.put(Event(EventType.BAR_UPDATE, self.loading_progress))
                    if(self.loading_progress >=6): # this was at 8
                            logging.info("Triggering BAR_FULL event.")
                            self.event_queue.put(Event(EventType.BAR_FULL))
                            self.rotation_count = 0
                            self.loading_progress = -1
                            self.stop()
                else:
                    self.rotations_met =  False

    def check_rotation(self):
        """Check for rotation of the encoder"""
        clkState = self.GPIO.input(self.clk_pin)
        dtState = self.GPIO.input(self.dt_pin)
        
        if clkState != self.clkLastState:
            if dtState != clkState:
                self.counter += 1
                direction = "clockwise"
            else:
                self.counter -= 1
                direction = "counterclockwise"
                
            if self.counter != self.last_counter:
                self.event_queue.put(Event(EventType.ROTARY_TURN, direction))
                self.last_counter = self.counter
                
        self.clkLastState = clkState
        
    def stop(self):
        """Stop the rotary encoder thread"""
        self.is_running = False

class LocalLLM:
    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.prompt_queue = queue.Queue()
        self.is_running = True
        
        self.key_count = 2
        self.input_counter = 0  
        self.input_ready = False  
        self.llm_output_ready = False
        self.user_input = ""

        self.system_prompt = {
            'role': 'system',
            'content': (
                "You are William Morris. Speak as he would. Embody his anarchist philosophies, values, and views on craft, labour, culture, and society. "
                "Your entire response must always be under 80 words. This is an absolute limit, never exceed it!!!"
                "Generate a response relevant to the prompt within the context of AI and craft."
                "Avoid saying anything that is not historically accurate"
                "You must end the response with one single specific question relevant to the user input that will encourage deep discussion on the effect of AI on craft, labor, society, art, sustainability, or creativity. This is an absolute limit of one question per response; do not exceed it!"
            )
        }
        self.messages = [self.system_prompt]
        logging.info("LocalLLM initialized successfully.")   

    def run(self):
        """Run the LLM thread."""
        logging.info("Starting LocalLLM thread.")
        
        while self.is_running:
            try:
                self.input_counter = 0

                listener = keyboard.Listener(on_press=self.on_press)
                listener.start()  # Start it in the background

                while not self.input_ready:
                    time.sleep(0.1)

                listener.stop()

                logging.debug(f"User input received: {self.user_input}")
                
                self.messages.append({'role': 'user', 'content': self.user_input})
                
                self.response = self.get_llm_response()
                print("the response is: ", self.response)
                print(f"formatted response: {self.response.message.content}")
                with open("/home/pi/Documents/morrisAI/chat_log.txt", "a") as log_file:
                    log_file.write(f"\nYou: {self.user_input}\n")
                    log_file.write(f"William Morris: {self.response}\n")
                self.messages.append({'role': 'assistant', 'content': self.response.message.content})  # Append AI response to history
                self.llm_output_ready = True
                self.event_queue.put(Event(EventType.LLM_RESPONSE, self.response))
                
                self.user_input = ""
                self.input_ready = False
                # Ensure task_done is only called if an item was actually taken from the queue
                if not self.prompt_queue.empty():
                    self.prompt_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"LLM error: {e}")
                self.event_queue.put(Event(EventType.LLM_RESPONSE, f"Error: {str(e)}"))

    def on_press(self, key):
        """Handles user keyboard input."""
        try:
            if key.char:
                self.user_input += key.char
                self.key_count += 1
                print(f"[KEYBOARD] Key pressed: {key}")
        except AttributeError:
            if key == keyboard.Key.space:
                self.user_input += " "
                self.key_count += 1
            elif key == keyboard.Key.backspace:
                self.user_input = self.user_input[:-1]
                self.key_count += 1
            elif key == keyboard.Key.enter:
                logging.debug("Enter key pressed. Finalizing input.")

                with self.event_queue.mutex:
                     self.event_queue.queue.clear()

                print("Queue jumped! Processing only the latest input.")
                
                self.key_count = 0  # Reset counter on Enter
                self.event_queue.put(Event(EventType.INPUTSENT))
                self.input_ready = True

        if self.key_count >= 3:
            logging.debug("Sending KEYBOARDINPUT event.")
            self.event_queue.put(Event(EventType.KEYBOARDINPUT))
            self.input_counter += 1
            self.key_count = 0

    def get_llm_response(self):
        """Get response from local LLM API."""
        logging.info(f"Sending input to LLM: {self.user_input[:30]}...")

        try:

            self.options = {'num_predict': 135}

            response = chat(model='llama3.2:1b', messages=self.messages, options=self.options)  # Get response object
            self.llm_output_ready = True  
            logging.debug(f"Response received: {response}")  # Print the full response
            
            return response  

        except Exception as e:
            logging.error(f"Error getting LLM response: {e}")
            return f"Error: {str(e)}"

    def stop(self):
        """Stop the LLM thread."""
        logging.info("Stopping LocalLLM thread.")
        self.is_running = False

# Example usage
if __name__ == "__main__":
    controller = DeviceController()
    try:
        controller.start()
    except KeyboardInterrupt:
        print("Program terminated by user")
