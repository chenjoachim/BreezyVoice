import argparse
import os
import sys
import re
from functools import partial
import time

import torch
torch.set_num_threads(1)
import torchaudio
import torchaudio.functional as F
import whisper
import opencc
from hyperpyyaml import load_hyperpyyaml
from huggingface_hub import snapshot_download
from g2pw import G2PWConverter

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
print(ROOT_DIR)
sys.path.append(ROOT_DIR)
sys.path.append('{}/third_party/Matcha-TTS'.format(ROOT_DIR))

from cosyvoice.cli.frontend import CosyVoiceFrontEnd
from cosyvoice.cli.model import CosyVoiceModel
from cosyvoice.cli.cosyvoice import CosyVoice
from cosyvoice.utils.file_utils import load_wav
from cosyvoice.utils.frontend_utils import (contains_chinese, replace_blank, replace_corner_mark,remove_bracket, spell_out_number, split_paragraph)
from utils.word_utils import word_to_dataset_frequency, char2phn, phn2char, always_augment_chars


import pydub
import numpy as np



####new normalize
class CustomCosyVoiceFrontEnd(CosyVoiceFrontEnd):
    def text_normalize_new(self,text, split=False):
        text = text.strip()
        def split_by_brackets(input_string):
            # Use regex to find text inside and outside the brackets
            inside_brackets = re.findall(r'\[(.*?)\]', input_string)
            outside_brackets = re.split(r'\[.*?\]', input_string)

            # Filter out empty strings from the outside list (result of consecutive brackets)
            outside_brackets = [part for part in outside_brackets if part]

            return inside_brackets, outside_brackets

        def text_normalize_no_split(text, is_last=False):
            text = text.strip()
            text_is_terminated = text[-1] == "。"
            if contains_chinese(text):
                #print(text)
                if self.use_ttsfrd:
                    text = self.frd.get_frd_extra_info(text, 'input')
                else:
                    text = self.zh_tn_model.normalize(text)
                if not text_is_terminated and not is_last:
                    text = text[:-1]
                #print(text)
                text = text.replace("\n", " ")
                text = replace_blank(text)
                text = replace_corner_mark(text)
                text = text.replace(".", "、")
                #print(text)
                text = text.replace(" - ", "，")
                #print(text)
                text = remove_bracket(text)
                #print(text)
                text = re.sub(r'[「」『』]', '', text)
                text = re.sub(r'[，,]+$', '。', text)
                #print(text)
            else:
                if self.use_ttsfrd:
                    text = self.frd.get_frd_extra_info(text, 'input')
                else:
                    text = self.en_tn_model.normalize(text)
                text = spell_out_number(text, self.inflect_parser)
            return text

        def join_interleaved(outside, inside):
            # Ensure the number of parts match between outside and inside
            result = []

            # Iterate and combine alternating parts
            for o, i in zip(outside, inside):
                result.append(o + '[' + i + ']')

            # Append any remaining part (if outside is longer than inside)
            if len(outside) > len(inside):
                result.append(outside[-1])

            return ''.join(result)
        inside_brackets, outside_brackets = split_by_brackets(text)
        #print("io",inside_brackets, outside_brackets)
        #text = re.sub(r'(\[[^\]]*\])(.*?)', normalize_outside_brackets, text)
        #print(text)
        for n in range(len(outside_brackets)):
            e_out = text_normalize_no_split(outside_brackets[n],is_last = n == len(outside_brackets) - 1)
            outside_brackets[n] = e_out
            time.sleep(0.05)

        text = join_interleaved(outside_brackets, inside_brackets)
        #print()

        # if contains_chinese(text):
        #     texts = [i for i in split_paragraph(
        #         text, partial(self.tokenizer.encode, allowed_special=self.allowed_special),
        #         "zh", token_max_n=80, token_min_n=60, merge_len=20, comma_split=False
        #     )]
        # else:
        #     texts = [i for i in split_paragraph(
        #         text, partial(self.tokenizer.encode, allowed_special=self.allowed_special),
        #         "en", token_max_n=80, token_min_n=60, merge_len=20, comma_split=False
        #     )]

        if split is False:
            return text
        return text # Should be texts

    def frontend_zero_shot(self, tts_text, prompt_text, prompt_speech_16k):
        tts_text_token, tts_text_token_len = self._extract_text_token(tts_text)
        prompt_text_token, prompt_text_token_len = self._extract_text_token(prompt_text)
        prompt_speech_22050 = torchaudio.transforms.Resample(orig_freq=16000, new_freq=22050)(prompt_speech_16k)
        speech_feat, speech_feat_len = self._extract_speech_feat(prompt_speech_22050)
        speech_token, speech_token_len = self._extract_speech_token(prompt_speech_16k)
        embedding = self._extract_spk_embedding(prompt_speech_16k)
        model_input = {'text': tts_text_token, 'text_len': tts_text_token_len,
                       'prompt_text': prompt_text_token, 'prompt_text_len': prompt_text_token_len,
                       'llm_prompt_speech_token': speech_token, 'llm_prompt_speech_token_len': speech_token_len,
                       'flow_prompt_speech_token': speech_token, 'flow_prompt_speech_token_len': speech_token_len,
                       'prompt_speech_feat': speech_feat, 'prompt_speech_feat_len': speech_feat_len,
                       'llm_embedding': embedding, 'flow_embedding': embedding}
        return model_input

    def frontend_zero_shot_dual(self, tts_text, prompt_text, prompt_speech_16k, flow_prompt_text, flow_prompt_speech_16k):
        tts_text_token, tts_text_token_len = self._extract_text_token(tts_text)
        prompt_text_token, prompt_text_token_len = self._extract_text_token(prompt_text)
        flow_prompt_text_token, flow_prompt_text_token_len = self._extract_text_token(flow_prompt_text)
        flow_prompt_speech_22050 = torchaudio.transforms.Resample(orig_freq=16000, new_freq=22050)(flow_prompt_speech_16k)
        speech_feat, speech_feat_len = self._extract_speech_feat(flow_prompt_speech_22050)

        flow_speech_token, flow_speech_token_len = self._extract_speech_token(flow_prompt_speech_16k)
        #speech_token, speech_token_len = self._extract_speech_token(prompt_speech_16k)
        speech_token = flow_speech_token.clone()
        speech_token_len = flow_speech_token_len.clone()
        embedding = self._extract_spk_embedding(prompt_speech_16k)
        #flow_embedding = self._extract_spk_embedding(flow_prompt_speech_16k)
        flow_embedding = embedding.clone()
        model_input = {'text': tts_text_token, 'text_len': tts_text_token_len,
                       'prompt_text': prompt_text_token, 'prompt_text_len': prompt_text_token_len,
                       'llm_prompt_speech_token': speech_token, 'llm_prompt_speech_token_len': speech_token_len,
                       'flow_prompt_speech_token': flow_speech_token, 'flow_prompt_speech_token_len': flow_speech_token_len,
                       'prompt_speech_feat': speech_feat, 'prompt_speech_feat_len': speech_feat_len,
                       'llm_embedding': embedding, 'flow_embedding': flow_embedding}
        return model_input

####model
class CustomCosyVoiceModel(CosyVoiceModel):

    def __init__(self,
                 llm: torch.nn.Module,
                 flow: torch.nn.Module,
                 hift: torch.nn.Module):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.llm = llm
        self.flow = flow
        self.hift = hift

    def load(self, llm_model, flow_model, hift_model):
        self.llm.load_state_dict(torch.load(llm_model, map_location=self.device))
        self.llm.to(self.device).eval()
        self.flow.load_state_dict(torch.load(flow_model, map_location=self.device))
        self.flow.to(self.device).eval()
        self.hift.load_state_dict(torch.load(hift_model, map_location=self.device))
        self.hift.to(self.device).eval()

    def inference(self, text, text_len, flow_embedding, llm_embedding=torch.zeros(0, 192),
                  prompt_text=torch.zeros(1, 0, dtype=torch.int32), prompt_text_len=torch.zeros(1, dtype=torch.int32),
                  llm_prompt_speech_token=torch.zeros(1, 0, dtype=torch.int32), llm_prompt_speech_token_len=torch.zeros(1, dtype=torch.int32),
                  flow_prompt_speech_token=torch.zeros(1, 0, dtype=torch.int32), flow_prompt_speech_token_len=torch.zeros(1, dtype=torch.int32),
                  prompt_speech_feat=torch.zeros(1, 0, 80), prompt_speech_feat_len=torch.zeros(1, dtype=torch.int32)):
        tts_speech_token = self.llm.inference(text=text.to(self.device),
                                              text_len=text_len.to(self.device),
                                              prompt_text=prompt_text.to(self.device),
                                              prompt_text_len=prompt_text_len.to(self.device),
                                              prompt_speech_token=llm_prompt_speech_token.to(self.device),
                                              prompt_speech_token_len=llm_prompt_speech_token_len.to(self.device),
                                              embedding=llm_embedding.to(self.device),
                                              beam_size=1,
                                              sampling=25,
                                              max_token_text_ratio=30,
                                              min_token_text_ratio=3)

        #input()

        tts_mel = self.flow.inference(token=tts_speech_token,
                                      token_len=torch.tensor([tts_speech_token.size(1)], dtype=torch.int32).to(self.device),
                                      prompt_token=flow_prompt_speech_token.to(self.device),
                                      prompt_token_len=flow_prompt_speech_token_len.to(self.device),
                                      prompt_feat=prompt_speech_feat.to(self.device),
                                      prompt_feat_len=prompt_speech_feat_len.to(self.device),
                                      embedding=flow_embedding.to(self.device))
        tts_speech = self.hift.inference(mel=tts_mel).cpu()
        torch.cuda.empty_cache()
        return {'tts_speech': tts_speech}

###CosyVoice
class CustomCosyVoice:

    def __init__(self, model_dir):
        #assert os.path.exists(model_dir), f"model path '{model_dir}' not exist, please check the path: pretrained_models/CosyVoice-300M-zhtw"
        instruct = False

        if not os.path.exists(model_dir):
            model_dir = snapshot_download(model_dir)
        print("model", model_dir)
        self.model_dir = model_dir

        with open('{}/cosyvoice.yaml'.format(model_dir), 'r') as f:
            configs = load_hyperpyyaml(f)
        self.frontend = CustomCosyVoiceFrontEnd(configs['get_tokenizer'],
                                          configs['feat_extractor'],
                                          model_dir,
                                          '{}/campplus.onnx'.format(model_dir),
                                          '{}/speech_tokenizer_v1.onnx'.format(model_dir),
                                          '{}/spk2info.pt'.format(model_dir),
                                          instruct,
                                          configs['allowed_special'])
        self.model = CosyVoiceModel(configs['llm'], configs['flow'], configs['hift'])
        self.model.load('{}/llm.pt'.format(model_dir),
                        '{}/flow.pt'.format(model_dir),
                        '{}/hift.pt'.format(model_dir))
        del configs

    def list_avaliable_spks(self):
        spks = list(self.frontend.spk2info.keys())
        return spks

    def inference_sft(self, tts_text, spk_id):
        tts_speeches = []
        for i in self.frontend.text_normalize(tts_text, split=True):
            model_input = self.frontend.frontend_sft(i, spk_id)
            model_output = self.model.inference(**model_input)
            tts_speeches.append(model_output['tts_speech'])
        return {'tts_speech': torch.concat(tts_speeches, dim=1)}

    def inference_zero_shot(self, tts_text, prompt_text, prompt_speech_16k):
        prompt_text = self.frontend.text_normalize(prompt_text, split=False)
        tts_speeches = []
        for i in self.frontend.text_normalize(tts_text, split=True):
            model_input = self.frontend.frontend_zero_shot(i, prompt_text, prompt_speech_16k)
            model_output = self.model.inference(**model_input)
            tts_speeches.append(model_output['tts_speech'])
        return {'tts_speech': torch.concat(tts_speeches, dim=1)}

    def inference_zero_shot_no_unit_condition_no_normalize(self, tts_text, prompt_text, prompt_speech_16k, flow_prompt_text = None, flow_prompt_speech_16k = None):
        if flow_prompt_text == None:
            flow_prompt_text = prompt_text
        if flow_prompt_speech_16k == None:
            flow_prompt_speech_16k = prompt_speech_16k
        prompt_text = prompt_text
        tts_speeches = []
        for i in re.split(r'(?<=[？！。.?!])\s*', tts_text):
            if not len(i):
                continue
            model_input = self.frontend.frontend_zero_shot_dual(i, prompt_text, prompt_speech_16k, flow_prompt_text, flow_prompt_speech_16k)
            print(model_input.keys())
            model_input["llm_prompt_speech_token"] = model_input["llm_prompt_speech_token"][:,:0]
            model_input["llm_prompt_speech_token_len"][0] = 0
            model_output = self.model.inference(**model_input)
            tts_speeches.append(model_output['tts_speech'])
        return {'tts_speech': torch.concat(tts_speeches, dim=1)}

    def inference_zero_shot_no_normalize(self, tts_text, prompt_text, prompt_speech_16k, max_length=-1, task_id="12345678")->pydub.AudioSegment:
        prompt_text = prompt_text
        temp_audio = pydub.AudioSegment.silent(duration=0)
        final_audio = pydub.AudioSegment.silent(duration=0)
        total_duration = 0
        unsaved_duration = 0
        chunk_id = 0
        temp_files = []
        target_seconds = max_length * 60 if max_length > 0 else float('inf')
        THRESHOLD = 50  # Adjust this value based on your needs

        for i in re.split(r'(?<=[？！。.?!])\s*', tts_text):
            if not len(i):
                continue
            
            # If sentence is longer than threshold, split by commas
            if len(i) >= THRESHOLD:
                comma_splits = re.split(r'(?<=，|,)\s*', i)
                sub_sentences = [comma_splits[0]]
                for split in comma_splits[1:]:
                    if len(sub_sentences[-1]) + len(split) < THRESHOLD:
                        sub_sentences[-1] += split
                    else:
                        if sub_sentences[-1].endswith(('，', ',')):
                            sub_sentences[-1] = sub_sentences[-1][:-1] + "。"
                        else:
                            sub_sentences[-1] = sub_sentences[-1] + "。"
                        sub_sentences.append(split)
                for sub_sentence in sub_sentences:
                    if not len(sub_sentence):
                        continue
                    # print("Synthesizing:", sub_sentence)
                    model_input = self.frontend.frontend_zero_shot(sub_sentence, prompt_text, prompt_speech_16k)
                    model_output = self.model.inference(**model_input)
                    output_duration = model_output['tts_speech'].shape[1]/22050
                    
                    total_duration += output_duration
                    unsaved_duration += output_duration
                    
                    audio_numpy = model_output['tts_speech'].squeeze().cpu().numpy()
                    audio_numpy = (audio_numpy * 32767).astype(np.int16)
                    
                    segment = pydub.AudioSegment(
                        audio_numpy.tobytes(),
                        frame_rate=22050,
                        sample_width=2,
                        channels=1
                    )
                    
                    temp_audio += segment
            else:
                # Original code for shorter sentences
                # print("Synthesizing:", i)
                model_input = self.frontend.frontend_zero_shot(i, prompt_text, prompt_speech_16k)
                model_output = self.model.inference(**model_input)
                output_duration = model_output['tts_speech'].shape[1]/22050
                
                total_duration += output_duration
                unsaved_duration += output_duration
                
                audio_numpy = model_output['tts_speech'].squeeze().cpu().numpy()
                audio_numpy = (audio_numpy * 32767).astype(np.int16)
                
                segment = pydub.AudioSegment(
                    audio_numpy.tobytes(),
                    frame_rate=22050,
                    sample_width=2,
                    channels=1
                )
                
                temp_audio += segment
            
            # The rest of your code for saving audio segments
            if unsaved_duration > 3 * 60:
                temp_filename = os.path.join("tmp", f"{task_id}_{chunk_id:02d}.mp3")
                chunk_id += 1
                temp_files.append(temp_filename)
                temp_audio.export(
                    temp_filename,
                    format="mp3",
                    bitrate="128k",
                    parameters=["-ac", "1", "-ar", "44100"],
                    codec="libmp3lame"
                )
                temp_audio = pydub.AudioSegment.silent(0)
                unsaved_duration = 0
            
            # print("Current duration:", total_duration)
            
            if total_duration > target_seconds:
                break
            
        print("Total duration:", total_duration)
        if unsaved_duration > 0:
            os.makedirs("tmp", exist_ok=True)
            temp_filename = os.path.join("tmp", f"{task_id}_{chunk_id:02d}.mp3")
            chunk_id += 1
            temp_files.append(temp_filename)
            temp_audio.export(
                temp_filename,
                format="mp3",
                bitrate="128k",
                parameters=["-ac", "1", "-ar", "44100"],
                codec="libmp3lame"
            )
            temp_audio = pydub.AudioSegment.silent(0)
            unsaved_duration = 0

        for file in temp_files:
            segment = pydub.AudioSegment.from_file(file)
            final_audio += segment
            os.remove(file)

        return final_audio

####wav2text
def transcribe_audio(audio_file):
    #model = whisper.load_model("base")
    #result = model.transcribe(audio_file)
    from transformers import pipeline

    # Load Whisper model
    whisper_asr = pipeline("automatic-speech-recognition", model="openai/whisper-base")

    # Perform ASR on an audio file
    result = whisper_asr(audio_file)

    converter = opencc.OpenCC('s2t')
    traditional_text = converter.convert(result["text"])
    return traditional_text

def get_bopomofo_rare(text, converter):
    res = converter(text)
    text_w_bopomofo = [x for x in zip(list(text), res[0])]
    reconstructed_text = ""

    for i in range(len(text_w_bopomofo)):
        t = text_w_bopomofo[i]
        try:
            next_t_char = text_w_bopomofo[i+1][0]
        except:
            next_t_char = None
        #print(t[0], word_to_dataset_frequency[t[0]], t[1])

        if word_to_dataset_frequency[t[0]] < 500 and t[1] != None and next_t_char != '[':
            # Add the char and the pronunciation
            reconstructed_text += t[0] + f"[:{t[1]}]"

        elif len(char2phn[t[0]]) >= 2:
            if t[1] != char2phn[t[0]][0] and next_t_char != '[':
                if t[1] in phn2char:
                    # There is a frequent word with same deterministic pronounciation
                    reconstructed_text += phn2char[t[1]] + f"[:{t[1]}]"
                elif (word_to_dataset_frequency[t[0]] < 2000 or t[0] in always_augment_chars) :  # Not most common pronunciation
                    # Add the char and the pronunciation
                    reconstructed_text += t[0] + f"[:{t[1]}]"
                else:
                    reconstructed_text += t[0]
            else:
                reconstructed_text += t[0]
            #print("DEBUG, multiphone char", t[0], char2phn[t[0]])
        else:
            # Add only the char
            reconstructed_text += t[0]

    #print("Reconstructed:", reconstructed_text)
    return reconstructed_text

def get_bopomofo(text, converter, chunk_size=30, sleeptime=0.1):
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    reconstructed_text = ""
    for chunk in chunks:
        reconstructed_text += get_bopomofo_rare(chunk, converter)
        time.sleep(sleeptime)
    return reconstructed_text

import re

def parse_transcript(text, end):
    pattern = r"<\|(\d+\.\d+)\|>([^<]+)<\|(\d+\.\d+)\|>"
    matches = re.findall(pattern, text)

    parsed_output = [(float(start), float(end), content.strip()) for start, content,end in matches]
    count0 = 0
    for i in range(len(parsed_output)):
        if parsed_output[i][0] == 0:
            count0 += 1
        if count0 >= 2:
            parsed_output = parsed_output[:i]
            break
    #print("a", parsed_output)
    for i in range(len(parsed_output)):
        if parsed_output[i][0] >= end:
            parsed_output = parsed_output[:i]
            break
    #print("b", parsed_output)
    for i in range(len(parsed_output)):
        if parsed_output[i][0] < end - 15:
            continue
        else:
            parsed_output = parsed_output[i:]
            break
    #print("c", parsed_output)
    start = parsed_output[0][0]
    parsed_output = "".join([p[2] for p in parsed_output])
    return parsed_output, start

def single_inference(speaker_prompt_audio_path, content_to_synthesize, output_path, cosyvoice, bopomofo_converter, speaker_prompt_text_transcription=None, max_length=-1, bitrate="128k", sample_rate=44100):
    prompt_speech_16k = load_wav(speaker_prompt_audio_path, 16000)
    # Estimate the amount of content to synthesize and give a raw cut
    if max_length > 0:
        max_length_word = max_length * 360
        if max_length_word < len(content_to_synthesize):
            content_to_synthesize = content_to_synthesize[:max_length_word]
    output_path = output_path.strip()

    if speaker_prompt_text_transcription:
        speaker_prompt_text_transcription = speaker_prompt_text_transcription
    else:
        speaker_prompt_text_transcription = transcribe_audio(speaker_prompt_audio_path)



    ###normalization
    print("[DEBUG] Normalizing transcription...")
    speaker_prompt_text_transcription = cosyvoice.frontend.text_normalize_new(
        speaker_prompt_text_transcription,
        split=False
    )
    print("[DEBUG] Normalizing content...")
    content_to_synthesize = cosyvoice.frontend.text_normalize_new(
        content_to_synthesize,
        split=False
    )
    print("[DEBUG] Finished normalization.")
    speaker_prompt_text_transcription_bopomo = get_bopomofo(speaker_prompt_text_transcription, bopomofo_converter)
    # speaker_prompt_text_transcription_bopomo = speaker_prompt_text_transcription
    # print("Speaker prompt audio transcription:",speaker_prompt_text_transcription_bopomo)

    #print("Content to be synthesized before bopomofo:",content_to_synthesize)
    content_to_synthesize_bopomo = get_bopomofo(content_to_synthesize, bopomofo_converter)
    # content_to_synthesize_bopomo = content_to_s3ynthesize
    task_id = os.path.basename(output_path).split(".")[0]
    # print("Content to be synthesized:",content_to_synthesize_bopomo)
    start = time.time()
    output = cosyvoice.inference_zero_shot_no_normalize(content_to_synthesize_bopomo, speaker_prompt_text_transcription_bopomo, prompt_speech_16k, max_length=max_length, task_id=task_id)
    end = time.time()
    print("Elapsed time:",end - start)
    # print("Generated audio length:", output['tts_speech'].shape[1]/22050, "seconds")
    # waveform = output["tts_speech"]
    # if sample_rate != 22050:
    #     resampler = torchaudio.transforms.Resample(22050, sample_rate)
    #     waveform = resampler(waveform)
    # torchaudio.save(output_path, output['tts_speech'], 22050)

    output.export(output_path,
        format="mp3",
        bitrate=bitrate,
        parameters=["-ac", "1", "-ar", str(sample_rate)],
        codec="libmp3lame"
    )

    print(f"Generated voice saved to {output_path}")

def main():
    ####args
    parser = argparse.ArgumentParser(description="Run BreezyVoice text-to-speech with custom inputs")
    parser.add_argument("--content_to_synthesize", type=str, required=True, help="Specifies the content that will be synthesized into speech.")
    parser.add_argument("--speaker_prompt_audio_path", type=str, required=True, help="Specifies the path to the prompt speech audio file of the speaker.")
    parser.add_argument("--speaker_prompt_text_transcription", type=str, required=False, help="Specifies the transcription of the speaker prompt audio (Highly Recommended, if not provided, the system will fall back to transcribing with Whisper.)")

    parser.add_argument("--output_path", type=str, required=False, default="results/output.wav", help="Specifies the name and path for the output .wav file.")

    parser.add_argument("--model_path", type=str, required=False, default = "MediaTek-Research/BreezyVoice-300M",help="Specifies the model used for speech synthesis.")
    parser.add_argument("--content_type", type=str, choices=["file", "text"], default="text", help="Specifies the type of content to be synthesized.")
    parser.add_argument("--max_length", type=int, default=-1, help="Specifies the maximum length of the synthesized speech in minutes.")
    args = parser.parse_args()


    cosyvoice = CustomCosyVoice(args.model_path)

    bopomofo_converter = G2PWConverter()
    # bopomofo_converter = None


    speaker_prompt_audio_path = args.speaker_prompt_audio_path
    if args.content_type == "file":
        with open(args.content_to_synthesize, "r") as f:
            content_to_synthesize = f.read()
    else:
        content_to_synthesize = args.content_to_synthesize
    output_path = args.output_path.strip()
    single_inference(speaker_prompt_audio_path, content_to_synthesize, output_path, cosyvoice, bopomofo_converter, args.speaker_prompt_text_transcription, max_length=args.max_length)

if __name__ == "__main__":
    main()
