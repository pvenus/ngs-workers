"""Pure, versioned HQ music contract. No model imports, DBs or filesystem writes."""
import copy
import json
import math
import os
import re

MODEL = 'acestep-xl-sft-lm4b'
PROFILE_VERSION = 'ace-xl-sft-lm4b.v1'
RUNTIME_REVISION = 'ca1e85fe9430179831e6bc6be790c332190a3866'
LM_REVISION = '0a3ec94b557aea7d508da38b31cfe7341f6ff737'
XL_REVISION = 'd06de46b4622f781cf07f4a013a67d591ca52819'
SETTINGS = {'instrumental', 'lyrics', 'bpm', 'keyscale', 'timesignature', 'vocal_language'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def enabled():
    return os.environ.get('AUDIOFORGE_HQ_ENABLED') == '1'


def require_enabled():
    if not enabled():
        raise RuntimeError('HQ_PROFILE_NOT_ACTIVATED')


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - SETTINGS:
        raise ValueError('HQ_SETTINGS_UNSUPPORTED')
    instrumental = value.get('instrumental', True)
    if type(instrumental) is not bool:
        raise ValueError('HQ_INSTRUMENTAL_BOOLEAN_REQUIRED')
    lyrics = value.get('lyrics', '')
    if not isinstance(lyrics, str) or len(lyrics) > 4000 or '\0' in lyrics:
        raise ValueError('HQ_LYRICS_INVALID')
    if (instrumental and lyrics) or (not instrumental and not lyrics.strip()):
        raise ValueError('HQ_LYRICS_INSTRUMENTAL_CONFLICT')
    if 'bpm' in value and (type(value['bpm']) is not int or not 30 <= value['bpm'] <= 300):
        raise ValueError('HQ_BPM_INVALID')
    if 'keyscale' in value and (not isinstance(value['keyscale'], str) or not value['keyscale'].strip() or len(value['keyscale']) > 32 or '\0' in value['keyscale']):
        raise ValueError('HQ_KEYSCALE_INVALID')
    if 'timesignature' in value and (type(value['timesignature']) is not str or value['timesignature'] not in {'2', '3', '4', '6', '7'}):
        raise ValueError('HQ_TIMESIGNATURE_INVALID')
    if 'vocal_language' in value and (not isinstance(value['vocal_language'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,16}', value['vocal_language'])):
        raise ValueError('HQ_VOCAL_LANGUAGE_INVALID')
    return copy.deepcopy(value)


def validate_call(prompt, duration, seed, model, settings=None, kind='music'):
    if model != MODEL or kind != 'music':
        raise ValueError('HQ_EXPLICIT_MUSIC_PROFILE_REQUIRED')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000 or '\0' in prompt:
        raise ValueError('HQ_PROMPT_INVALID')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 10 <= duration <= 30:
        raise ValueError('HQ_DURATION_10_TO_30_REQUIRED')
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError('HQ_SEED_INVALID')
    return validate_settings({} if settings is None else settings)


def validate_payload(value):
    allowed = {'prompt', 'duration_seconds', 'seed', 'model', 'kind', 'idempotency_key',
               'generation_settings', 'title', 'use_case', 'brief', 'prompt_version'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('HQ_REQUEST_FIELDS_UNSUPPORTED')
    validate_call(value.get('prompt'), value.get('duration_seconds', 3), value.get('seed', 0),
                  value.get('model'), value.get('generation_settings', {}), value.get('kind', 'sfx'))
    canonical(value)  # Reject nonfinite/nondurable nested metadata before queue insertion.
    return value
