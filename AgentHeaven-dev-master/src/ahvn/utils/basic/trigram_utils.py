from .misc_utils import get_total_deepsize_of

import time
import json


class trigram:
    def __init__(
        self,
        hsh_da=None,
        threshold=250,
        tri_gram=False,
    ):
        """
        这是一个hash-based子串搜索方式
        采用稀疏n-gram方法：
            以trigram的hash值大小为关键字，在所有n-gram中，
            仅保留最左最右的trigram的hash严格大于中间所有trigram的hash值的n-gram
            依据单调栈，询问串会被拆分为至多2*len(que_str)个n-gram，数据串大约被拆分成O(log_2(len_add))个n-gram
            当询问串的n-gram列表完全包含数据串的n-gram列表，那就有大概率包含该子串
        状态压缩判包含方法：
            将数据串的所有n-gram子串编号1,2,4,8...
            当询问串完全包含数据串时，那么他包含的此id下所有n-gram子串编号二进制或之和应为2^len_add-1，记为dic_full[id]
        阈值threshold忽视无用n-gram：
            部分n-gram出现次数非常多，但并不包含多少有用信息，例如“有限公司”，以及作为子串出现的“中国电信”（作为全串出现需要保留）
            出现次数超过阈值的子串视为无有效信息子串，在查询时不进行搜索，以减少搜索时间

        hsh_da       字符串的hash方式，默认为crc32
        threshold       界定无用子串的阈值，
        tri_gram        是否仅采用trigram而不采用稀疏n-gram方法
        """

        if hsh_da is None:
            hsh_da = "crc32"
        self.hsh_da = hsh_da
        self.threshold = threshold
        self.tri_gram = tri_gram
        self._clear()

    def _hash_build(self):
        """
        创建hash函数，并初始化待分配id
        """

        if self.hsh_da not in {"crc32", "md5", "sha256"}:
            raise Exception

        if self.hsh_da == "crc32":
            from zlib import crc32

            def hash_crc32(data):
                return crc32(str.encode(str(data)))

            self.hash_data = hash_crc32

        if self.hsh_da == "md5":
            from hashlib import md5

            def hash_md5(data):
                return int(md5(str.encode(str(data))).hexdigest(), 16)

            self.hash_data = hash_md5

        if self.hsh_da == "sha256":
            from hashlib import sha256

            def hash_sha256(data):
                return int(sha256(str.encode(str(data))).hexdigest(), 16)

            self.hash_data = hash_sha256

        if not callable(self.hash_data):
            raise Exception

        self.rand_id = int(time.time() * 10000)

    def _clear(self):
        """
        dic_bigram          长度为2,3的数据串单独记录
        dic_ngram           长度大于等于4的稀疏n-gram
        dic                 id到串的映射，一个id仅对应一个子串
        dic_str             串到id的反映射，一个子串可对应多个id
        dic_full            每个id对应的n-gram子串编号二进制或之和满值
        dic_det             已超阈值的子串集合
        rand_id             待分配id
        """

        self.dic_bigram = set()
        self.dic_ngram = dict()
        self.dic = dict()
        self.dic_str = dict()
        self.dic_full = dict()
        self.dic_det = set()
        self._hash_build()

    def close(self):
        self.flush()
        self._clear()

    def __sizeof__(self) -> int:
        return get_total_deepsize_of(self.dic_bigram, self.dic_ngram, self.dic, self.dic_str, self.dic_full, self.dic_det)

    def check(self, hsh):
        """
        查询一个n-gram的hash值是否出现过
        如果出现过，并且出现次数超阈值则进行标记
        """

        if hsh not in self.dic_ngram:  # 未加入n-gram
            return False
        if hsh in self.dic_det:  # 已标记n-gram
            return True
        if len(self.dic_ngram[hsh]) < self.threshold:  # 未超阈值n-gram
            return True
        for id, type_id in self.dic_ngram[hsh]:
            self.dic_full[id] ^= type_id
        self.dic_det.add(hsh)
        return True

    def add(self, add_str: str, id=None):
        """
        插入一个编号为id的数据串add_str
        若无id则分配一个id并返回这个id
        """
        len_add = len(add_str)

        if len_add < 2:  # 长度1的子串不予考虑
            return None

        if id is None:  # 无id时分配一个id
            while self.rand_id in self.dic:
                self.rand_id += 1
            id = self.rand_id
        if isinstance(id, (tuple, list)):
            id = "_".join(map(str, id))

        self.dic[id] = add_str

        if add_str in self.dic_str:  # 已插入重复子串，不同编号
            self.dic_str[add_str].append(id)
            return id

        self.dic_str[add_str] = [id]

        if len_add < 4:
            self.dic_bigram.add(add_str)
            return True

        hash_list = [self.hash_data(add_str[i : i + 3]) for i in range(len_add - 2)]

        if self.tri_gram:  # 仅使用trigram做hash验证
            hsh_list = list(set(hash_list))

        else:
            max_hash = max(hash_list)
            hash_max = hash_list[0]

            hsh_list = []

            las = 0
            for i in range(1, len_add - 2):
                hash_max = max(hash_max, hash_list[i])
                if hash_list[i] == max_hash or hash_list[i] == hash_max:
                    hsh = self.hash_data(add_str[las : i + 3])
                    hsh_list.append(hsh)
                    las = i

            las = len_add
            hash_max = hash_list[len_add - 3]
            for i in range(len_add - 4, -1, -1):
                hash_max = max(hash_max, hash_list[i])
                if hash_list[i] == hash_max:
                    hsh = self.hash_data(add_str[i:las])
                    hsh_list.append(hsh)
                    las = i + 3

            hsh_list = list(set(hsh_list))

        len_hsh = len(hsh_list)

        self.dic_full[id] = (1 << len_hsh) - 1

        for i in range(len_hsh):  # 将 n-gram 状态压缩插入到dic_ngram中
            hsh = hsh_list[i]
            if hsh not in self.dic_ngram:
                self.dic_ngram[hsh] = []
            self.dic_ngram[hsh].append((id, 1 << i))
            if hsh in self.dic_det:
                self.dic_full[id] ^= 1 << i

        return id

    def get_result(self, results, que_str):
        """
        验证经过hash找到的串是否真的为子串并找到出现位置
        """
        result = []
        for res in results:
            position = que_str.find(res)
            if position != -1:
                result.append((res, position, self.dic_str[res]))
        return result

    def query(self, que_str: str):
        """
        查询que_str的所有出现过子串，以及这些子串第一次出现的位置和对应的编号集合
        """
        len_que = len(que_str)

        result = []

        for i in range(0, len_que - 1):
            if que_str[i : i + 2] in self.dic_bigram:
                result.append(que_str[i : i + 2])

        for i in range(0, len_que - 2):
            if que_str[i : i + 3] in self.dic_bigram:
                result.append(que_str[i : i + 3])

        if len_que < 4:
            return self.get_result(result, que_str)

        hash_list = [self.hash_data(que_str[i : i + 3]) for i in range(len_que - 2)]

        if self.tri_gram:  # 仅使用trigram做hash验证
            hsh_list = list(set(hash_list))

        else:
            hsh_list = []
            stack = [(hash_list[0], 0)]

            for i in range(1, len_que - 2):
                while stack and stack[-1][0] <= hash_list[i]:
                    data = que_str[stack[-1][1] : i + 3]
                    hsh = self.hash_data(data)
                    self.check(hsh)
                    if hsh in self.dic_det:
                        if data in self.dic_str:
                            result.append(que_str[stack[-1][1] : i + 3])
                    else:
                        hsh_list.append(hsh)
                    stack.pop()
                if stack:
                    data = que_str[stack[-1][1] : i + 3]
                    hsh = self.hash_data(data)
                    self.check(hsh)
                    if hsh in self.dic_det:
                        if data in self.dic_str:
                            result.append(que_str[stack[-1][1] : i + 3])
                    else:
                        hsh_list.append(hsh)
                stack.append((hash_list[i], i))

            hsh_list = list(set(hsh_list))

        result_hsh = dict()

        for hsh in hsh_list:  # 验证包含的id 的 n-gram 是否全出现
            if hsh in self.dic_ngram:
                for id, type_id in self.dic_ngram[hsh]:
                    if id in result_hsh:
                        result_hsh[id] |= type_id
                    else:
                        result_hsh[id] = type_id

        for id, type_id in result_hsh.items():
            if type_id == self.dic_full[id]:
                result.append(self.dic[id])

        return self.get_result(result, que_str)

    def save(self, path: str = None):
        meta_data = {"hsh_da": self.hsh_da, "threshold": self.threshold, "tri_gram": self.tri_gram}
        meta_data["dic_bigram"] = list(x for x in self.dic_bigram)
        meta_data["dic_ngram"] = list(x for x in self.dic_ngram.items())
        meta_data["dic"] = list(x for x in self.dic.items())
        meta_data["dic_str"] = list(x for x in self.dic_str.items())
        meta_data["dic_full"] = list(x for x in self.dic_full.items())
        meta_data["dic_det"] = list(x for x in self.dic_det)
        # for x in meta_data.items():
        #     print(x)
        # print(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)

    def load(path: str = None):
        new_tri = trigram()
        with open(path, "r", encoding="utf-8") as f:
            meta_data = json.load(f)
        # for x in meta_data.items():
        #     print(x)
        new_tri.hsh_da = meta_data["hsh_da"]
        new_tri.threshold = meta_data["threshold"]
        new_tri.tri_gram = meta_data["tri_gram"]
        new_tri.dic_bigram = set(meta_data["dic_bigram"])
        new_tri.dic_ngram = dict(meta_data["dic_ngram"])
        new_tri.dic = dict(meta_data["dic"])
        new_tri.dic_str = dict(meta_data["dic_str"])
        new_tri.dic_full = dict(meta_data["dic_full"])
        new_tri.dic_det = set(meta_data["dic_det"])
        new_tri._hash_build()
        return new_tri

    def build(self):
        """
        检视所有子串并标记出现次数超阈值的子串
        """
        for hsh in self.dic_ngram.keys():
            self.check(hsh)


def main():
    tri = trigram()
    # print(dir(tri))
    tri.add("ab", 1)
    tri.add("cd", 2)
    tri.add("fdbgdcjcbbaigiej", (3, 1))
    tri.add("edffncheiffdjemkjkg", 4)
    tri.add("abc", 5)
    tri.add("eibgjhefjbjagdjbijbegabfdaae", 6)
    tri.add("mkhnmfdgdhdkgkjkjkkccicdnefamce", 7)
    tri.add("ffibjhdjggfdhhachcicjc", 8)
    tri.add("hlkknbmhlnknbmnkid", 9)
    tri.add("abcde", 10)
    tri.add("bcdef", 11)
    tri.add("hijkl", 12)
    tri.add("abcde")
    tri.add("bcdef")
    tri.add("hijkl")
    tri.add("mkhnmfdgdhdkgkjkjkkccicdnefamce")
    tri.add("ffibjhdjggfdhhachcicjc")
    print(tri.query("abcdefghijklmn"))
    print(tri.query("fdbgdcjcbbaigiejhcjcehddiaffhfjcjcjchaeibgjhefjbjagdjbijbegabfdaaechejhihihcggffibjhdjggfdhhachcicjc"))
    print(tri.query("edffncheiffdjemkjkgmigefgghoegbabhfobfkjkimlmkhnmfdgdhdkgkjkjkkccicdnefamcecedajikhlkknbmhlnknbmnkid"))
    tri.save("test.json")
    ntri = trigram.load("test.json")
    print(ntri.query("abcdefghijklmn"))
    print(ntri.query("fdbgdcjcbbaigiejhcjcehddiaffhfjcjcjchaeibgjhefjbjagdjbijbegabfdaaechejhihihcggffibjhdjggfdhhachcicjc"))
    print(ntri.query("edffncheiffdjemkjkgmigefgghoegbabhfobfkjkimlmkhnmfdgdhdkgkjkjkkccicdnefamcecedajikhlkknbmhlnknbmnkid"))


if __name__ == "__main__":
    main()
