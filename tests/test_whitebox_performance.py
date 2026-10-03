import copy
import subprocess
from pathlib import Path

import pytest
from modules.whitebox import sample, validate_keys


def frames():
    return [dict(t=t,position=[0,0,0],left_hand=[-.2,1+t*.3,.2],expression=t/2,morph=t/2,neck_extension=.03*t,scale=[1,1+t,1]) for t in [0,2]]


def test_performance_interpolation_and_hold():
    keys=frames();validate_keys(keys,2)
    mid=sample(keys,1)
    assert mid['left_hand']==pytest.approx([-.2,1.3,.2])
    assert (mid['expression'],mid['morph'],mid['neck_extension'])==pytest.approx((.5,.5,.03))
    assert mid['scale']==[1,2,1]
    keys[0]['hold']=True
    assert sample(keys,1)['morph']==0


@pytest.mark.parametrize('channel,value',[('expression',1.1),('morph',-1),('neck_extension',.4),('left_hand',[1,2]),('scale',[1,0,1]),('head_yaw',2),('torso_yaw',-2),('body_roll',2)])
def test_invalid_performance_channels_rejected(channel,value):
    keys=frames();keys[1][channel]=value
    with pytest.raises(ValueError):validate_keys(keys,2)


def test_hand_channel_must_cover_entire_track():
    keys=frames();del keys[1]['left_hand']
    with pytest.raises(ValueError):validate_keys(keys,2)


def test_real_renderer_performance_and_rewind():
    subprocess.run(['node',str(Path(__file__).with_name('whitebox_performance_check.mjs'))],check=True,capture_output=True,text=True)

