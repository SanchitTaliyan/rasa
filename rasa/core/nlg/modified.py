import copy
import logging
from collections import defaultdict

from rasa.core.trackers import DialogueStateTracker
from typing import Text, Any, Dict, Optional, List

from rasa.core.nlg.generator import NaturalLanguageGenerator
from rasa.core.nlg.interpolator import interpolate_text, interpolate
from rasa.core.nlg.template import TemplatedNaturalLanguageGenerator

import tensorflow_hub as tfhub
import tensorflow as tf
import tensorflow_text
import numpy as np
import random


logger = logging.getLogger(__name__)

class ModifiedTemplateGenerator(TemplatedNaturalLanguageGenerator):
    """Natural language generator that generates messages based on templates.

    The templates can use variables to customize the utterances based on the
    state of the dialogue."""

    def __init__(self, templates: Dict[Text, List[Dict[Text, Any]]]) -> None:
        self.templates = templates
        self.modifiers = ["Nevermind.", "Ok.", "Yes, that's right.", "Actually no.", "Whatever.", "I'll repeat.", "Again. "]

    async def generate(
        self,
        template_name: Text,
        tracker: DialogueStateTracker,
        output_channel: Text,
        **kwargs: Any,
    ) -> Optional[Dict[Text, Any]]:
        """Generate a response for the requested template."""
        filled_slots = tracker.current_slot_values()
        template = self.generate_from_slots(
            template_name, filled_slots, output_channel, **kwargs
        )
        enhanced_text = self.prepend_and_rerank(template['text'], tracker)
        template['text'] = enhanced_text
        return template

    def generate_modified_texts(self, text):
        return [text] + [
            f"{m} {text}" for m in self.modifiers
        ]

    def rank(self, candidates, tracker):
        sess = None

        if sess is not None:
            sess.close()

        sess = tf.InteractiveSession(graph=tf.Graph())

        module = tfhub.Module("http://models.poly-ai.com/multi_context_convert/v1/model.tar.gz")


        text_placeholder = tf.placeholder(dtype=tf.string, shape=[None])
        extra_text_placeholder = tf.placeholder(dtype=tf.string, shape=[None])
        context_encoding_tensor = module(
            {
                'context': text_placeholder,
                'extra_context': extra_text_placeholder,
            },
            signature="encode_context"
        )
        response_encoding_tensor = module(text_placeholder, signature="encode_response")

        encoding_dim = int(context_encoding_tensor.shape[1])

        sess.run(tf.tables_initializer())
        sess.run(tf.global_variables_initializer())
        
        def encode_context(dialogue_history):
            """Encode the dialogue context to the response ranking vector space.

            Args:
                dialogue_history: a list of strings, the dialogue history, in
                    chronological order.
            """

            # The context is the most recent message in the history.
            context = dialogue_history[-1]

            extra_context = list(dialogue_history[:-1])
            extra_context.reverse()
            extra_context_feature = " ".join(extra_context)

            return sess.run(
                context_encoding_tensor,
                feed_dict={
                    text_placeholder: [context],
                    extra_text_placeholder: [extra_context_feature],
                }
            )[0]

        def encode_responses(texts):
            return sess.run(response_encoding_tensor, feed_dict={text_placeholder: texts})

        # Encode the responses in batches of 64.
        batch_size = 64
        response_encodings = []
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            response_encodings.append(encode_responses(batch))
        #response_encodings = encode_responses([candidates])

        response_encodings = np.concatenate(response_encodings)

        dialogue_history = self.extract_history(tracker)

        context_encoding = encode_contexts(dialogue_history)
        scores = context_encoding.dot(response_encodings.T)
        top_idx = np.argsort(scores)[::-1]
        for i in range(len(candidates)):
            idx = top_idx[i]
            logger.error(f"[{float(scores[idx])}] {candidates[idx]}")

        scores = None
        sorted_candidates = [candidates[i] for i in top_idx]
        return scores, sorted_candidates

    def extract_history(self, tracker):
        from rasa.core.events import UserUttered, BotUttered
        turns = []
        for event in tracker.events:
            if isinstance(event, UserUttered) or isinstance(event, BotUttered):
                turns.append(event.text)
        return turns
    
    def prepend_and_rerank(self, text, tracker):
        candidates = self.generate_modified_texts(text)
        scores, candidates = self.rank(candidates, tracker)
        return candidates[0]

if __name__ == "__main__":
    sess = None

    if sess is not None:
        sess.close()


    sess = tf.InteractiveSession(graph=tf.Graph())

    module = tfhub.Module("http://models.poly-ai.com/convert/v1/model.tar.gz")

    text_placeholder = tf.placeholder(dtype=tf.string, shape=[None])
    context_encoding_tensor = module(text_placeholder, signature="encode_context")
    response_encoding_tensor = module(text_placeholder, signature="encode_response")

    encoding_dim = int(context_encoding_tensor.shape[1])
    print(f"ConveRT encodes contexts & responses to {encoding_dim}-dimensional vectors")

    sess.run(tf.tables_initializer())
    sess.run(tf.global_variables_initializer())

    def encode_contexts(texts):
        return sess.run(context_encoding_tensor, feed_dict={text_placeholder: texts})

    def encode_responses(texts):
        return sess.run(response_encoding_tensor, feed_dict={text_placeholder: texts})

    import numpy as np
    import random

    modifiers = ["Ok. ", "Actually, No.", "Yes, that's right"]

    responses = []
    fname = "/Users/alan/Developer/dialog/carbon-bot/convert/faq.txt"
    with open(fname) as f:
        for line in f:
            l = line.strip()
            responses.append(l)
            for m in modifiers:
                responses.append(f"{m} {l}")

    print("\n\t- ".join(["Three random facts:"] + random.sample(responses, 3)))

    # Encode the responses in batches of 64.
    batch_size = 64
    response_encodings = []
    for i in range(0, len(responses), batch_size):
        batch = responses[i:i + batch_size]
        response_encodings.append(encode_responses(batch))

    response_encodings = np.concatenate(response_encodings)
    print(f"Encoded {response_encodings.shape[0]} candidate responses.")

    context = "should I thank you?" # @param {type:"string"}
    if context:
        context_encoding = encode_contexts([context])
        scores = np.dot(response_encodings, context_encoding.T)
        top_index = np.argmax(scores)
        top_score = float(scores[top_index])
        print(f"[{top_score:.3f}] {responses[top_index]}")