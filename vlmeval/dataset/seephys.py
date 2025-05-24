import os
import re
import tempfile
from functools import partial
import pandas as pd
from .image_base import ImageBaseDataset
from .utils import build_judge, DEBUG_MESSAGE
from ..smp import *
from ..utils import track_progress_rich


class SeePhys(ImageBaseDataset):
    TYPE = 'VQA'
    DATASET_URL = {
        'SeePhys': 'SeePhys.tsv',
    }

    def build_prompt(self, line):
        if isinstance(line, int):
            line = self.data.iloc[line]

        if self.meta_only:
            tgt_path = toliststr(line['image_path'])
        else:
            tgt_path = self.dump_image(line)

        question = "" if str(line['question']) == 'nan' else line['question']

        if os.environ.get('USE_CAPTION', '0') == '1':
            question += line['caption']

        if os.environ.get('USE_SEARCH', '0') == '1':
            if line['language'] == 'English':
                question += "\nPlease search the Internet to answer the above question. First output your thinking process in <think> </think> tags and then output the final answer in <answer> </answer> tags."  # noqa: E501
            else:
                question += "\n请在互联网上搜索以回答上述问题。首先在<think></think>标签中输出你的思维过程，然后在<answer></answer>标签中输入最终答案。"
        elif os.environ.get('USE_COT_PROMPT', '1') == '1':
            if line['language'] == 'English':
                question += "\nPlease answer this question with reasoning. First output your reasoning process in <think> </think> tags and then output the final answer in <answer> </answer> tags."
            else:
                question += "\n请用推理来回答这个问题。首先在<think></think>标签中输出推理过程，然后在<answer></answer>标签中输入最终答案。"
        else:
            if line['language'] == 'English':
                question += "\n请不要进行推理，直接用数字、公式或短语回答这个问题。"
            else:
                question += "\n"
        try:
            if line['sig_figs']:
                sf = str(int(line['sig_figs']))
                if line['language'] == 'English':
                    question += f"The final answer should retain {sf} significant figures."
                else:
                    question += f"最终答案应保留{sf}位有效数字。"
        except Exception as e:
            pass
        msgs = []
        if os.environ.get('USE_IMAGE', '1') == '1':
            if isinstance(tgt_path, list):
                msgs.extend([dict(type='image', value=p) for p in tgt_path])
            else:
                msgs = [dict(type='image', value=tgt_path)]
        msgs.append(dict(type='text', value=question))
        return msgs

    @classmethod
    def evaluate(self, eval_file, **judge_kwargs):
        from .utils.seephys import extract, eval_acc
        model = judge_kwargs['model']
        suffix = eval_file.split('.')[-1]
        storage = eval_file.replace(f'.{suffix}', f'_{model}.xlsx')
        tmp_file = eval_file.replace(f'.{suffix}', f'_{model}.pkl')
        nproc = judge_kwargs.pop('nproc', 4)
        if not osp.exists(storage):
            data = load(eval_file)
            model = build_judge(max_tokens=1024, **judge_kwargs)
            assert model.working(), ('SeePhys evaluation requires a working OPENAI API\n' + DEBUG_MESSAGE)
            lt = len(data)
            lines = [data.iloc[i] for i in range(lt)]
            tups = [(model, line) for line in lines]
            indices = [line['index'] for line in lines]
            ans = {}
            if osp.exists(tmp_file):
                ans = load(tmp_file)
            tups = [x for x, i in zip(tups, indices) if i not in ans]
            indices = [i for i in indices if i not in ans]

            if len(indices):
                new_results = track_progress_rich(
                    extract,
                    tups,
                    nproc=nproc,
                    chunksize=nproc,
                    keys=indices,
                    save=tmp_file,
                )
                ans = load(tmp_file)
                for k, v in zip(indices, new_results):
                    assert k in ans
                    assert ans[k]['log'] == v['log'] and ans[k]['extract'] == v['extract'] and ans[k]['score'] == v[
                        'score']

            data['extract'] = [ans[idx]['extract'] for idx in data['index']]
            data['log'] = [ans[idx]['log'] for idx in data['index']]
            data['score'] = [ans[idx]['score'] for idx in data['index']]

            dump(data, storage)

        score = eval_acc(storage)
        score_pth = storage.replace('.xlsx', '_score.json')
        dump(score, score_pth)
        return score