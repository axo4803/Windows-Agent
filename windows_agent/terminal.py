"""Collect a whole multiline task before sending it to the model."""
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.validation import Validator

def submit(event):
    event.current_buffer.validate_and_handle()

def session(input=None, output=None):
    keys = KeyBindings()
    keys.add('enter')(submit)
    keys.add('c-d')(submit)

    @keys.add('escape', 'enter')
    def newline(event):
        event.current_buffer.insert_text('\n')

    return PromptSession(multiline=True, key_bindings=keys, input=input, output=output,
                         validator=Validator.from_callable(lambda text: len(text) <= 12000,
                             error_message='This task exceeds 12000 characters. Shorten it before sending.'),
                         prompt_continuation='... ', enable_history_search=False)
