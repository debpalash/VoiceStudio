from services.segmentation import deduplicate_chunk_segments


def segment(id, words, speaker='Speaker 1'):
    return {'id':id, 'start':words[0]['start'], 'end':words[-1]['end'], 'words':words,
            'text':' '.join(w['text'] for w in words), 'speaker_id':speaker}


def words(text, start):
    return [{'text':w,'start':start+i*.4,'end':start+(i+1)*.4} for i,w in enumerate(text.split())]


def test_repeated_chunk_context_is_removed_without_losing_the_new_tail():
    a=segment('a',words('Thank you very much',10))
    b=segment('b',words('Thank you very much Welcome everyone',10))
    result=deduplicate_chunk_segments([a,b])
    assert result[1]['text']=='Welcome everyone'
    assert result[1]['start']==11.6
    assert a['text']=='Thank you very much'


def test_exact_duplicate_removed_but_other_speaker_preserved():
    a=segment('a',words('Thank you very much',10))
    b={**a,'id':'b'}
    assert len(deduplicate_chunk_segments([a,b]))==1
    assert len(deduplicate_chunk_segments([a,{**b,'speaker_id':'Speaker 2'}]))==2


def test_repeated_phrase_at_different_time_is_not_a_duplicate():
    a=segment('a',words('Thank you very much',10))
    b=segment('b',words('Thank you very much',12))
    assert deduplicate_chunk_segments([a,b])==[a,b]


def test_fix_bounds_only_when_words_prove_speech_is_disjoint():
    a=segment('a',words('Good morning everyone',10))
    b=segment('b',words('Nice to meet you',12))
    a['end']=13
    result=deduplicate_chunk_segments([a,b])
    assert result[0]['end']==11.2
    assert a['end']==13
    c=segment('c',words('Other simultaneous words',10.5),'Speaker 2')
    assert deduplicate_chunk_segments([a,c])==[a,c]


def test_out_of_order_stitched_word_cannot_invert_or_delete_a_line():
    a=segment('a',words('You are so young',10))
    a['end']=12.2
    a['words'].append({'text':'Earlier?','start':8,'end':9})
    b=segment('b',words('How old are you',12))
    result=deduplicate_chunk_segments([a,b])
    assert len(result)==2
    assert result[0]['start']==10
    assert result[0]['end']==11.6
