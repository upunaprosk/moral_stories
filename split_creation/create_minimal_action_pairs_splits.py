import os
import json
import spacy
import argparse

import numpy as np

from string import punctuation
from nltk.corpus import stopwords
from pyxdameraulevenshtein import normalized_damerau_levenshtein_distance


def _lemmatize(line, remove_stopwords):
    """ Helper function for obtaining various word representations """

    # strip, replace special tokens
    orig_line = line
    line = line.strip()
    # Remove double space
    line = ' '.join(line.split())
    # Tokenize etc.
    line_nlp = nlp(line)
    spacy_tokens = [elem.text for elem in line_nlp]
    spacy_tokens_lower = [elem.text.lower() for elem in line_nlp]
    spacy_lemmas = list()
    for elem in line_nlp:
        if elem.lemma_ == '-PRON-' or elem.lemma_.isdigit():
            spacy_lemmas.append(elem.lower_)
        else:
            spacy_lemmas.append(elem.lemma_.lower().strip())

    # Generate a mapping between whitespace tokens and SpaCy tokens
    ws_tokens = orig_line.strip().split()
    ws_to_spacy_map = dict()
    spacy_to_ws_map = dict()
    ws_loc = 0
    ws_tok = ws_tokens[ws_loc]

    for spacy_loc, spacy_tok in enumerate(spacy_tokens):
        while True:
            # Map whitespace tokens to be identical to spacy tokens
            if spacy_tok == ws_tok or spacy_tok in ws_tok:
                # Terminate
                if ws_loc >= len(ws_tokens):
                    break

                # Extend maps
                if not ws_to_spacy_map.get(ws_loc, None):
                    ws_to_spacy_map[ws_loc] = list()
                ws_to_spacy_map[ws_loc].append(spacy_loc)
                if not spacy_to_ws_map.get(spacy_loc, None):
                    spacy_to_ws_map[spacy_loc] = list()
                spacy_to_ws_map[spacy_loc].append(ws_loc)

                # Move pointer
                if spacy_tok == ws_tok:
                    ws_loc += 1
                    if ws_loc < len(ws_tokens):
                        ws_tok = ws_tokens[ws_loc]
                else:
                    ws_tok = ws_tok[len(spacy_tok):]
                break
            else:
                ws_loc += 1

    # Assert full coverage of whitespace and SpaCy token sequences by the mapping
    ws_covered = sorted(list(ws_to_spacy_map.keys()))
    spacy_covered = sorted(list(set(list([val for val_list in ws_to_spacy_map.values() for val in val_list]))))
    assert ws_covered == [n for n in range(len(ws_tokens))], \
        'WS-SpaCy mapping does not cover all whitespace tokens: {}; number of tokens: {}'\
        .format(ws_covered, len(ws_tokens))
    assert spacy_covered == [n for n in range(len(spacy_tokens))], \
        'WS-SpaCy mapping does not cover all SpaCy tokens: {}; number of tokens: {}' \
        .format(spacy_covered, len(spacy_tokens))

    if remove_stopwords:
        # Filter out stopwords
        nsw_spacy_lemmas = list()
        for tok_id, tok in enumerate(spacy_tokens_lower):
            if tok not in STOP_WORDS and spacy_lemmas[tok_id] not in STOP_WORDS:
                nsw_spacy_lemmas.append(spacy_lemmas[tok_id])
            else:
                nsw_spacy_lemmas.append('<STPWRD>')

        spacy_lemmas = nsw_spacy_lemmas

    return spacy_lemmas, ws_tokens, spacy_to_ws_map


def split_based_on_action_distance(tsv_path, top_list_size, split_size, check_norm_overlap):
    """ Splits collected stories into train / dev / test based on edit distance between actions. """

    # Relevant fields
    loc_hit_id = 14

    loc_prompt1 = 27
    loc_prompt2 = 28
    loc_prompt3 = 29
    loc_prompt_pick = 40

    loc_situation = 37
    loc_intention = 39
    loc_moral_action = 31
    loc_good_consequence = 35
    loc_immoral_action = 30
    loc_bad_consequence = 34

    # Initialize containers
    stories = dict()
    actions_edit_distance = dict()
    moral_action_freqs = dict()
    immoral_action_freqs = dict()
    norm_lemmas = dict()

    # Populate tables
    print('Reading-in AMT results ...')
    with open(tsv_path, 'r', encoding='utf8') as tsv_file:
        for row_id, row in enumerate(tsv_file):
            if row_id == 0:
                continue

            # Read row
            joint_row = row
            row = row.split('\t')
            moral_action = row[loc_moral_action].strip()
            moral_action = moral_action.translate(str.maketrans(' ', ' ', punctuation))
            immoral_action = row[loc_immoral_action].strip()
            immoral_action = immoral_action.translate(str.maketrans(' ', ' ', punctuation))

            # Compute WORD-LEVEL edit distance between actions
            actions_edit_distance[row_id] = \
                normalized_damerau_levenshtein_distance(moral_action.split(), immoral_action.split())

            if top_list_size > 0 or check_norm_overlap:
                # Lemmatize actions and extend tables
                moral_action_lemmas, moral_action_tokens, good_spacy_to_ws_map = _lemmatize(moral_action, True)
                immoral_action_lemmas, immoral_action_tokens, bad_spacy_to_ws_map = _lemmatize(immoral_action, True)
                for gl in moral_action_lemmas:
                    if moral_action_freqs.get(gl, None) is None:
                        moral_action_freqs[gl] = 0
                    moral_action_freqs[gl] += 1
                for bl in immoral_action_lemmas:
                    if immoral_action_freqs.get(bl, None) is None:
                        immoral_action_freqs[bl] = 0
                    immoral_action_freqs[bl] += 1

                # Store story
                stories[row_id] = [joint_row, row,
                                   moral_action_lemmas, immoral_action_lemmas,
                                   moral_action_tokens, immoral_action_tokens,
                                   good_spacy_to_ws_map, bad_spacy_to_ws_map]

                if check_norm_overlap:
                    # Identify norm
                    prompt1 = row[loc_prompt1].strip()
                    prompt2 = row[loc_prompt2].strip()
                    prompt3 = row[loc_prompt3].strip()
                    prompt_pick = row[loc_prompt_pick].strip()
                    if '1' in prompt_pick:
                        norm = prompt1
                    elif '2' in prompt_pick:
                        norm = prompt2
                    else:
                        norm = prompt3
                    norm = norm.translate(str.maketrans(' ', ' ', punctuation))
                    norm_lemmas[row_id] = _lemmatize(norm, True)

            else:
                # Store story
                stories[row_id] = [joint_row, row]

    # Sort stories by the edit distance between the actions
    sorted_dist_diff_tpls = sorted([(story_id, dist) for story_id, dist in actions_edit_distance.items()],
                                   reverse=False, key=lambda x: x[1])

    # Detect bias terms
    top_biased_lemmas_dict = dict()
    if top_list_size > 0:
        print('Isolating biased terms ...')
        term_freq_diffs = dict()
        for lem in moral_action_freqs.keys():
            if lem == '<STPWRD>':
                continue
            if immoral_action_freqs.get(lem, None) is None:
                term_freq_diffs[lem] = moral_action_freqs[lem]
            else:
                term_freq_diffs[lem] = moral_action_freqs[lem] - immoral_action_freqs[lem]
        for lem in immoral_action_freqs.keys():
            if moral_action_freqs.get(lem, None) is None:
                term_freq_diffs[lem] = -immoral_action_freqs[lem]

        # Sort by frequency difference
        freq_diff_tpls = sorted([(lem, freq, np.abs(freq)) for lem, freq in term_freq_diffs.items()],
                                reverse=True, key=lambda x: x[2])
        top_biased_lemma_tpls = freq_diff_tpls[:top_list_size]
        top_biased_lemmas_dict = {tpl[0]: tpl[1] for tpl in top_biased_lemma_tpls}

    # Build splits
    print('-' * 20)
    print('Creating splits ...')

    # Select stories for test and dev splits, first
    sorted_story_ids, sorted_action_distances = zip(*sorted_dist_diff_tpls)
    good_norm_overlap = list()
    bad_norm_overlap = list()
    # Keep track of action distance
    train_distances = list()
    dev_distances = list()
    test_distances = list()
    if top_list_size > 0:
        test_story_ids = list()
        dev_story_ids = list()
        train_story_ids = list()
        for iid, story_id in enumerate(sorted_story_ids):
            # Check which lemmas differ between actions
            _, row, good_lemmas, bad_lemmas, good_tokens, bad_tokens, good_map, bad_map = stories[story_id]
            unique_good_lemmas = list(set(good_lemmas) - set(bad_lemmas))
            unique_bad_lemmas = list(set(bad_lemmas) - set(good_lemmas))
            biased_good_lemmas = [lem for lem in unique_good_lemmas if top_biased_lemmas_dict.get(lem, 0) > 0]
            biased_bad_lemmas = [lem for lem in unique_bad_lemmas if top_biased_lemmas_dict.get(lem, 0) < 0]
            if check_norm_overlap:
                good_norm_overlap = list(set(norm_lemmas[story_id][0]) & set(unique_good_lemmas))
                bad_norm_overlap = list(set(norm_lemmas[story_id][0]) & set(unique_bad_lemmas))

            # If the contrasting lemmas are morally charged, place story in test, otherwise place it in dev / train
            # Optionally, if norm overlaps with either action, place story in dev / train
            is_in_test = False
            if len(biased_good_lemmas) == 0 and len(biased_bad_lemmas) == 0:
                if not check_norm_overlap or (check_norm_overlap and (len(good_norm_overlap) == 0 and
                                                                      len(bad_norm_overlap) == 0)):
                    if len(test_story_ids) < split_size:
                        test_story_ids.append(story_id)
                        test_distances.append(sorted_action_distances[iid])
                        is_in_test = True
            if not is_in_test:
                if len(dev_story_ids) < split_size:
                    dev_story_ids.append(story_id)
                    dev_distances.append(sorted_action_distances[iid])
                else:
                    train_story_ids.append(story_id)
                    train_distances.append(sorted_action_distances[iid])
    else:
        if check_norm_overlap:
            test_story_ids = list()
            dev_story_ids = list()
            train_story_ids = list()
            for iid, story_id in enumerate(sorted_story_ids):
                # Check which lemmas differ between actions
                _, row, good_lemmas, bad_lemmas, good_tokens, bad_tokens, good_map, bad_map = stories[story_id]
                unique_good_lemmas = list(set(good_lemmas) - set(bad_lemmas))
                unique_bad_lemmas = list(set(bad_lemmas) - set(good_lemmas))
                good_norm_overlap = list(set(norm_lemmas[story_id][0]) & set(unique_good_lemmas))
                bad_norm_overlap = list(set(norm_lemmas[story_id][0]) & set(unique_bad_lemmas))
                # If norm overlaps with either action, place story in dev / train
                if len(good_norm_overlap) == 0 and len(bad_norm_overlap) == 0:
                    if len(test_story_ids) < split_size:
                        test_story_ids.append(story_id)
                        test_distances.append(sorted_action_distances[iid])
                else:
                    if len(dev_story_ids) < split_size:
                        dev_story_ids.append(story_id)
                        dev_distances.append(sorted_action_distances[iid])
                    else:
                        train_story_ids.append(story_id)
                        train_distances.append(sorted_action_distances[iid])
        else:
            test_story_ids = sorted_story_ids[: split_size]
            test_distances = sorted_action_distances[: split_size]

            dev_story_ids = sorted_story_ids[split_size: split_size * 2]
            dev_distances = sorted_action_distances[split_size: split_size * 2]

            train_story_ids = sorted_story_ids[split_size * 2:]
            train_distances = sorted_action_distances[split_size * 2:]

    # Write to files
    for task in ['action_cls', 'action+norm_cls', 'action+context_cls', 'action+context+consequence_cls',
                 'action|context_gen', 'action|context+consequence_gen',
                 'norm|actions_gen', 'norm|actions+context_gen', 'norm|actions+context+consequences_gen',
                 'consequence+action_cls', 'consequence+action+context_cls',
                 'consequence|action_gen', 'consequence|action+context_gen']:
        tsv_dir = '/'.join(tsv_path.split('/')[:-1])
        task_dir = os.path.join(tsv_dir, task)
        challenge_dir = os.path.join(task_dir, 'lexical_bias')
        if not os.path.exists(task_dir):
            os.mkdir(task_dir)
        if not os.path.exists(challenge_dir):
            os.mkdir(challenge_dir)

        train_path = os.path.join(challenge_dir, 'train.jsonl')
        dev_path = os.path.join(challenge_dir, 'dev.jsonl')
        test_path = os.path.join(challenge_dir, 'test.jsonl')

        for story_ids, path in [(train_story_ids, train_path),
                                (dev_story_ids, dev_path),
                                (test_story_ids, test_path)]:
            lines = list()
            # Parse story
            for s_id in story_ids:
                story = stories[s_id][1]
                # Parse row
                if '1' in story[loc_prompt_pick].strip():
                    norm = story[loc_prompt1].strip()
                elif '2' in story[loc_prompt_pick].strip():
                    norm = story[loc_prompt2].strip()
                else:
                    norm = story[loc_prompt3].strip()

                hit_id = story[loc_hit_id].strip()
                situation = story[loc_situation].strip()
                intention = story[loc_intention].strip()
                moral_action = story[loc_moral_action].strip()
                good_consequence = story[loc_good_consequence].strip()
                immoral_action = story[loc_immoral_action].strip()
                bad_consequence = story[loc_bad_consequence].strip()

                # Construct split entry, depending on the task
                if task in ['action_cls', 'action+norm_cls', 'action+context_cls', 'action+context+consequence_cls',
                            'action|context_gen', 'action|context+consequence_gen',
                            'norm|actions_gen', 'norm|actions+context_gen',
                            'norm|actions+context+consequences_gen']:
                    line1 = {'qID': hit_id + '1',
                             'norm': norm,
                             'situation': situation,
                             'intention': intention,
                             'moral_action': moral_action,
                             'label': '1'}
                    line2 = {'qID': hit_id + '0',
                             'norm': norm,
                             'situation': situation,
                             'intention': intention,
                             'immoral_action': immoral_action,
                             'label': '0'}

                    if task in ['action_cls', 'action+norm_cls', 'norm|actions_gen']:
                        del line1['situation']
                        del line1['intention']
                        del line2['situation']
                        del line2['intention']
                    if task in ['action_cls']:
                        del line1['norm']
                        del line2['norm']

                    if task in ['action+context+consequence_cls', 'action|context+consequence_gen']:
                        line1['good_consequence'] = good_consequence
                        line2['bad_consequence'] = bad_consequence

                    if task in ['norm|actions_gen', 'norm|actions+context_gen',
                                'norm|actions+context+consequences_gen']:
                        line1['immoral_action'] = immoral_action
                        if task in ['norm|actions+context+consequences_gen']:
                            line1['good_consequence'] = good_consequence
                            line1['bad_consequence'] = bad_consequence
                        lines += [line1]
                    else:
                        lines += [line1, line2]

                else:
                    line1 = {'ID': hit_id + '1',
                             'norm': norm,
                             'situation': situation,
                             'intention': intention,
                             'moral_action': moral_action,
                             'good_consequence': good_consequence,
                             'label': '1'}
                    line2 = {'ID': hit_id + '2',
                             'norm': norm,
                             'situation': situation,
                             'intention': intention,
                             'moral_action': moral_action,
                             'bad_consequence': bad_consequence,
                             'label': '0'}
                    line3 = {'ID': hit_id + '3',
                             'norm': norm,
                             'situation': situation,
                             'intention': intention,
                             'immoral_action': immoral_action,
                             'bad_consequence': bad_consequence,
                             'label': '1'}
                    line4 = {'ID': hit_id + '4',
                             'norm': norm,
                             'situation': situation,
                             'intention': intention,
                             'immoral_action': immoral_action,
                             'good_consequence': good_consequence,
                             'label': '0'}

                    if task in ['consequence+action_cls', 'consequence|action_gen']:
                        for line in [line1, line2, line3, line4]:
                            del line['norm']
                            del line['situation']
                            del line['intention']
                    if task in ['consequence|action_gen', 'consequence|action+context_gen']:
                        lines += [line1, line3]
                    else:
                        lines += [line1, line2, line3, line4]

            # Dump to file
            with open(path, 'w', encoding='utf8') as out_f:
                for line_id, line in enumerate(lines):
                    if line_id > 0:
                        out_f.write('\n')
                    out_f.write(json.dumps(line))

            if path.endswith('train.jsonl'):
                print('{:s} TRAIN size: {:d}'.format(task, len(lines)))
                print('Mean TRAIN degree of isolation: {:.2f}'.format(np.mean(train_distances)))
                print('Saved to {:s}'.format(train_path))
                print('-' * 5)
            if path.endswith('dev.jsonl'):
                print('{:s} DEV size: {:d}'.format(task, len(lines)))
                print('Mean DEV degree of isolation: {:.2f}'.format(np.mean(dev_distances)))
                print('Saved to {:s}'.format(dev_path))
                print('-' * 5)
            if path.endswith('test.jsonl'):
                print('{:s} TEST size: {:d}'.format(task, len(lines)))
                print('Mean TEST degree of isolation: {:.2f}'.format(np.mean(test_distances)))
                print('Saved to {:s}'.format(test_path))
                print('-' * 5)


if __name__ == '__main__':
    # Instantiate processing pipeline
    nlp = spacy.load('en_core_web_sm', disable=['parser', 'textcat'])
    # Import stopword list
    STOP_WORDS = [w for w in stopwords.words('english') if w != 'not']

    parser = argparse.ArgumentParser()
    parser.add_argument('--stories_path', type=str, required=True,
                        help='path to file containing the collected stories')
    parser.add_argument('--top_list_size',
                        type=int, default=0, help='number of most frequent biased terms to consider')
    parser.add_argument('--split_size', type=int, default=1000, help='size of dev / test splits')
    parser.add_argument('--check_norm_overlap', action='store_true',
                        help='check whether either action has lemmas in common with the moral norm')
    args = parser.parse_args()

    split_based_on_action_distance(args.stories_path, args.top_list_size, args.split_size, args.check_norm_overlap)


