from pathlib import Path
from types import SimpleNamespace
from typing import (
    Any,
    List,
    MutableMapping,
    Sequence,
    Tuple,
    Sequence,
    Mapping,
    Union,
    Callable,
)
from collections import Counter
from datetime import datetime
import re
import warnings
import yaml
import pandas as pd


def read_pfs(filename, encoding="cp1252", unique_keywords=False):
    """Read a pfs file to a Pfs object for further analysis/manipulation

    Parameters
    ----------
    filename: str or Path
        File name including full path to the pfs file.
    encoding: str, optional
        How is the pfs file encoded? By default 'cp1252'
    unique_keywords: bool, optional
        Should the keywords in a section be unique? Some tools e.g. the
        MIKE Plot Composer allows non-unique keywords.
        If True: warnings will be issued if non-unique keywords
        are present and the first occurence will be used
        by default False

    Returns
    -------
    mikeio.Pfs
        Pfs object which can be used for inspection, manipulation and writing
    """
    return PfsDocument(filename, encoding=encoding, unique_keywords=unique_keywords)


class PfsNonUniqueList(list):
    pass


def _merge_dict(a: Mapping, b: Mapping, path: Sequence = None):
    """merges dict b into dict a; handling non-unique keys"""
    if path is None:
        path = []
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                _merge_dict(a[key], b[key], path + [str(key)])
            # elif a[key] == b[key]:
            #     pass  # same leaf value
            else:
                ab = list(a[key]) + list(b[key])
                a[key] = PfsNonUniqueList(ab)
        else:
            a[key] = b[key]
    return a


class _PfsBase(SimpleNamespace, MutableMapping):
    def __init__(self, dictionary, **kwargs):
        super().__init__(**kwargs)
        for key, value in dictionary.items():
            self.__set_key_value(key, value, copy=True)

    def __repr__(self) -> str:
        # return json.dumps(self.to_dict(), indent=2)
        # return yaml.dump(self.to_dict(), sort_keys=False)
        return "\n".join(self._to_txt_lines())

    def __len__(self):
        return len(self.__dict__)

    def __contains__(self, key):
        return key in self.keys()

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        self.__set_key_value(key, value)

    def __delitem__(self, key):
        if key in self.keys():
            self.__delattr__(key)
        else:
            raise IndexError("Key not found")

    def __set_key_value(self, key, value, copy=False):
        if value is None:
            value = {}

        if isinstance(value, dict):
            d = value.copy() if copy else value
            self.__setattr__(key, PfsSection(d))
        elif isinstance(value, PfsNonUniqueList):
            # multiple keywords/Sections with same name
            sections = PfsNonUniqueList()
            for v in value:
                if isinstance(v, dict):
                    d = v.copy() if copy else v
                    sections.append(PfsSection(d))
                else:
                    sections.append(self._parse_value(v))
            self.__setattr__(key, sections)
        else:
            self.__setattr__(key, self._parse_value(value))

    def _parse_value(self, v):
        if isinstance(v, str) and self._str_is_scientific_float(v):
            return float(v)
        return v

    @staticmethod
    def _str_is_scientific_float(s):
        """True: -1.0e2, 1E-4, -0.1E+0.5; False: E12, E-4"""
        if len(s) < 3:
            return False
        if (
            s.count(".") <= 2
            and s.lower().count("e") == 1
            and s.lower()[0] != "e"
            and s.strip()
            .lower()
            .replace(".", "")
            .replace("e", "")
            .replace("-", "")
            .replace("+", "")
            .isnumeric()
        ):
            try:
                float(s)
                return True
            except:
                return False
        else:
            return False

    def pop(self, key, *args):
        """If key is in the dictionary, remove it and return its
        value, else return default. If default is not given and
        key is not in the dictionary, a KeyError is raised."""
        return self.__dict__.pop(key, *args)

    def get(self, key, *args):
        """Return the value for key if key is in the PfsSection,
        else default. If default is not given, it defaults to None,
        so that this method never raises a KeyError."""
        return self.__dict__.get(key, *args)

    def clear(self):
        """Remove all items from the PfsSection."""
        return self.__dict__.clear()

    def keys(self):
        """Return a new view of the PfsSection's keys"""
        return self.__dict__.keys()

    def values(self):
        """Return a new view of the PfsSection's values."""
        return self.__dict__.values()

    def items(self):
        """Return a new view of the PfsSection's items ((key, value) pairs)"""
        return self.__dict__.items()

    # TODO: better name
    def update_recursive(self, key, value):
        """Update recursively all matches of key with value"""
        for k, v in self.items():
            if isinstance(v, _PfsBase):
                self[k].update_recursive(key, value)
            elif k == key:
                self[k] = value

    def search(
        self,
        text: str = None,
        *,
        key: str = None,
        section: str = None,
        param=None,
        case: bool = False,
    ):
        """Find recursively all keys, sections or parameters
           matching a pattern

        NOTE: logical OR between multiple conditions

        Parameters
        ----------
        text : str, optional
            Search for text in either key, section or parameter, by default None
        key : str, optional
            text pattern to seach for in keywords, by default None
        section : str, optional
            text pattern to seach for in sections, by default None
        param : str, bool, float, int, optional
            text or value in a parameter, by default None
        case : bool, optional
            should the text search be case-sensitive?, by default False

        Returns
        -------
        PfsSection
            Search result as a nested PfsSection
        """
        results = []
        if text is not None:
            assert key is None, "text and key cannot both be provided!"
            assert section is None, "text and section cannot both be provided!"
            assert param is None, "text and param cannot both be provided!"
            key = text
            section = text
            param = text
        key = key if (key is None or case) else key.lower()
        section = section if (section is None or case) else section.lower()
        param = (
            param
            if (param is None or not isinstance(param, str) or case)
            else param.lower()
        )
        for item in self._find_patterns_generator(
            keypat=key, parampat=param, secpat=section, case=case
        ):
            results.append(item)
        return self.__class__._merge_PfsSections(results) if len(results) > 0 else None

    def _find_patterns_generator(
        self, keypat=None, parampat=None, secpat=None, keylist=[], case=False
    ):
        """Look for patterns in either keys, params or sections"""
        for k, v in self.items():
            kk = str(k) if case else str(k).lower()

            if isinstance(v, _PfsBase):
                if secpat and secpat in kk:
                    yield from self._yield_deep_dict(keylist + [k], v)
                else:
                    yield from v._find_patterns_generator(
                        keypat, parampat, secpat, keylist=keylist + [k], case=case
                    )
            else:
                if keypat and keypat in kk:
                    yield from self._yield_deep_dict(keylist + [k], v)
                if self._param_match(parampat, v, case):
                    yield from self._yield_deep_dict(keylist + [k], v)

    @staticmethod
    def _yield_deep_dict(keys, val):
        """yield a deep nested dict with keys with a single deep value val"""
        for j in range(len(keys) - 1, -1, -1):
            d = {keys[j]: val}
            val = d
        yield d

    @staticmethod
    def _param_match(parampat, v, case):
        if parampat is None:
            return False
        if type(v) != type(parampat):
            return False
        if isinstance(v, str):
            vv = str(v) if case else str(v).lower()
            return parampat in vv
        else:
            return parampat == v

    def find_replace(self, old_value, new_value):
        """Update recursively all old_value with new_value"""
        for k, v in self.items():
            if isinstance(v, _PfsBase):
                self[k].find_replace(old_value, new_value)
            elif self[k] == old_value:
                self[k] = new_value

    def copy(self) -> "_PfsBase":
        """Return a copy of the PfsSection."""
        # is all this necessary???
        d = self.__dict__.copy()
        for key, value in d.items():
            if isinstance(value, _PfsBase):
                d[key] = value.to_dict().copy()
        return self.__class__(d)

    def _to_txt_lines(self):
        lines = []
        self._write_with_func(lines.append, newline="")
        return lines

    def _write_with_func(self, func: Callable, level: int = 0, newline: str = "\n"):
        """Write pfs nested objects

        Parameters
        ----------
        func : Callable
            A function that performs the writing e.g. to a file
        level : int, optional
            Level of indentation (add 3 spaces for each), by default 0
        newline : str, optional
            newline string, by default "\n"
        """
        lvl_prefix = "   "
        for k, v in self.items():

            # check for empty sections
            NoneType = type(None)
            if isinstance(v, NoneType):
                func(f"{lvl_prefix * level}[{k}]{newline}")
                func(f"{lvl_prefix * level}EndSect  // {k}{newline}{newline}")

            elif isinstance(v, List) and any(isinstance(subv, _PfsBase) for subv in v):
                # duplicate sections
                for subv in v:
                    if isinstance(subv, _PfsBase):
                        subsec = PfsSection({k: subv})
                        subsec._write_with_func(func, level=level, newline=newline)
                    else:
                        subv = self._prepare_value_for_write(subv)
                        func(f"{lvl_prefix * level}{k} = {subv}{newline}")
            elif isinstance(v, _PfsBase):
                func(f"{lvl_prefix * level}[{k}]{newline}")
                v._write_with_func(func, level=(level + 1), newline=newline)
                func(f"{lvl_prefix * level}EndSect  // {k}{newline}{newline}")
            elif isinstance(v, PfsNonUniqueList) or (
                isinstance(v, list) and all([isinstance(vv, list) for vv in v])
            ):
                if len(v) == 0:
                    # empty list -> keyword with no parameter
                    func(f"{lvl_prefix * level}{k} = {newline}")
                for subv in v:
                    subv = self._prepare_value_for_write(subv)
                    func(f"{lvl_prefix * level}{k} = {subv}{newline}")
            else:
                v = self._prepare_value_for_write(v)
                func(f"{lvl_prefix * level}{k} = {v}{newline}")

    def _prepare_value_for_write(self, v):
        """catch peculiarities of string formatted pfs data

        Parameters
        ----------
        v : str
            value from one pfs line

        Returns
        -------
            modified value
        """
        # some crude checks and corrections
        if isinstance(v, str):

            if len(v) > 5 and not ("PROJ" in v or "<CLOB:" in v):
                v = v.replace('"', "''")
                v = v.replace("\U0001F600", "'")

            if v == "":
                # add either '' or || as pre- and suffix to strings depending on path definition
                v = "''"
            elif v.count("|") == 2:
                v = f"{v}"
            else:
                v = f"'{v}'"

        elif isinstance(v, bool):
            v = str(v).lower()  # stick to MIKE lowercase bool notation

        elif isinstance(v, datetime):
            v = v.strftime("%Y, %m, %d, %H, %M, %S").replace(" 0", " ")

        elif isinstance(v, list):
            out = []
            for subv in v:
                out.append(str(self._prepare_value_for_write(subv)))
            v = ", ".join(out)

        return v

    def to_dict(self) -> dict:
        """Convert to (nested) dict (as a copy)"""
        d = self.__dict__.copy()
        for key, value in d.items():
            if isinstance(value, self.__class__):
                d[key] = value.to_dict()
        return d

    def to_dataframe(self, prefix: str = None) -> pd.DataFrame:
        """Output enumerated subsections to a DataFrame

        Parameters
        ----------
        prefix : str, optional
            The prefix of the enumerated sections, e.g. "File_",
            by default None (will try to "guess" the prefix)

        Returns
        -------
        pd.DataFrame
            The enumerated subsections as a DataFrame

        Examples
        --------
        >>> pfs = mikeio.read_pfs("lake.sw")
        >>> df = pfs.SW.OUTPUTS.to_dataframe(prefix="OUTPUT_")
        """
        if prefix is not None:
            sections = [
                k for k in self.keys() if k.startswith(prefix) and k[-1].isdigit()
            ]
            n_sections = len(sections)
        else:
            n_sections = -1
            # TODO: check that value is a PfsSection
            sections = [k for k in self.keys() if k[-1].isdigit()]
            for k in self.keys():
                if isinstance(k, str) and k.startswith("number_of_"):
                    n_sections = self[k]
            if n_sections == -1:
                # raise ValueError("Could not find a number_of_... keyword")
                n_sections = len(sections)

        if len(sections) == 0:
            prefix_txt = "" if prefix is None else f"(starting with '{prefix}') "
            raise ValueError(f"No enumerated subsections {prefix_txt}found")

        prefix = sections[0][:-1]
        res = []
        for j in range(n_sections):
            k = f"{prefix}{j+1}"
            res.append(self[k].to_dict())
        return pd.DataFrame(res, index=range(1, n_sections + 1))

    @classmethod
    def _merge_PfsSections(cls, sections: Sequence[Mapping]) -> "_PfsBase":
        """Merge a list of PfsSections/dict"""
        assert len(sections) > 0
        a = sections[0]
        for b in sections[1:]:
            a = _merge_dict(a, b)
        return cls(a)


class PfsSection(_PfsBase):
    def to_PfsDocument(self, name: str = None) -> "PfsDocument":
        """Convert to a PfsDocument in one of two ways:

        1) All key:value pairs will be targets (require all values to be PfsSections)
        2) Make this PfsSection the only target (requires a name)

        Parameters
        ----------
        name : str, optional
            Name of the target (=key that refer to this PfsSection)

        Returns
        -------
        PfsDocument
            A PfsDocument object
        """
        if name is None:
            return PfsDocument(self)
        else:
            return PfsDocument({name: self})

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, prefix: str) -> "PfsSection":
        """Create a PfsSection from a DataFrame"""
        d = {}
        for idx in df.index:
            key = prefix + str(idx)
            value = df.loc[idx].to_dict()
            d[key] = value
        return cls(d)


def parse_yaml_preserving_duplicates(src, unique_keywords=False):
    class PreserveDuplicatesLoader(yaml.loader.Loader):
        pass

    def map_constructor_duplicates(loader, node, deep=False):
        keys = [loader.construct_object(node, deep=deep) for node, _ in node.value]
        vals = [loader.construct_object(node, deep=deep) for _, node in node.value]
        key_count = Counter(keys)
        data = {}
        for key, val in zip(keys, vals):
            if key_count[key] > 1:
                if key not in data:
                    data[key] = PfsNonUniqueList()
                data[key].append(val)
            else:
                data[key] = val
        return data

    def map_constructor_duplicate_sections(loader, node, deep=False):
        keys = [loader.construct_object(node, deep=deep) for node, _ in node.value]
        vals = [loader.construct_object(node, deep=deep) for _, node in node.value]
        key_count = Counter(keys)
        data = {}
        for key, val in zip(keys, vals):
            if key_count[key] > 1:
                if isinstance(val, dict):
                    if key not in data:
                        data[key] = PfsNonUniqueList()
                    data[key].append(val)
                else:
                    warnings.warn(
                        f"Keyword {key} defined multiple times (first will be used). Value: {val}"
                    )
                    if key not in data:
                        data[key] = val
            else:
                data[key] = val
        return data

    constructor = (
        map_constructor_duplicate_sections
        if unique_keywords
        else map_constructor_duplicates
    )
    PreserveDuplicatesLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        constructor=constructor,
    )
    return yaml.load(src, PreserveDuplicatesLoader)


class PfsDocument(_PfsBase):
    """Create a PfsDocument object for reading, writing and manipulating pfs files

    Parameters
    ----------
    input: dict, PfsSection, str or Path
        Either a file name (including full path) to the pfs file
        to be read or dictionary-like structure.
    encoding: str, optional
        How is the pfs file encoded? By default cp1252
    unique_keywords: bool, optional
        Should the keywords in a section be unique? Some tools e.g. the
        MIKE Plot Composer allows non-unique keywords.
        If True: warnings will be issued if non-unique keywords
        are present and the first occurence will be used
        by default False
    """

    def __init__(self, input, encoding="cp1252", names=None, unique_keywords=False):

        if isinstance(input, (str, Path)) or hasattr(input, "read"):
            if names is not None:
                raise ValueError("names cannot be given as argument if input is a file")
            names, sections = self._read_pfs_file(input, encoding, unique_keywords)
        else:
            names, sections = self._parse_non_file_input(input, names)

        d = self._to_nonunique_key_dict(names, sections)
        super().__init__(d)

        self._ALIAS_LIST = ["_ALIAS_LIST"]  # ignore these in key list
        if self._is_FM_engine:
            self._add_all_FM_aliases()

    @staticmethod
    def _to_nonunique_key_dict(keys, vals):
        key_count = Counter(keys)
        data = {}
        for key, val in zip(keys, vals):
            if key_count[key] > 1:
                if key not in data:
                    data[key] = PfsNonUniqueList()
                data[key].append(val)
            else:
                data[key] = val
        return data

    def keys(self) -> List[str]:
        """Return a list of the PfsDocument's keys (target names)"""
        return [k for k, _ in self.items()]

    def values(self) -> List[Any]:
        """Return a list of the PfsDocument's values (targets)."""
        return [v for _, v in self.items()]

    def items(self) -> List[Tuple[str, Any]]:
        """Return a new view of the PfsDocument's items ((key, value) pairs)"""
        return [(k, v) for k, v in self.__dict__.items() if k not in self._ALIAS_LIST]

    @staticmethod
    def _unravel_items(items):
        rkeys = []
        rvals = []
        for k, v in items():
            if isinstance(v, PfsNonUniqueList):
                for subval in v:
                    rkeys.append(k)
                    rvals.append(subval)
            else:
                rkeys.append(k)
                rvals.append(v)
        return rkeys, rvals

    @property
    def data(self) -> Union[PfsSection, List[PfsSection]]:
        warnings.warn(
            FutureWarning(
                "The data attribute has been deprecated, please access the targets by their names instead."
            )
        )
        return self.targets[0] if self.n_targets == 1 else self.targets

    @property
    def targets(self) -> List[PfsSection]:
        """List of targets (root sections)"""
        _, rvals = self._unravel_items(self.items)
        return rvals

    @property
    def n_targets(self) -> int:
        """Number of targets (root sections)"""
        return len(self.targets)

    @property
    def is_unique(self) -> bool:
        """Are the target (root) names unique?"""
        return len(self.keys()) == len(self.names)

    @property
    def names(self) -> List[str]:
        """Names of the targets (root sections) as a list"""
        rkeys, _ = self._unravel_items(self.items)
        return rkeys

    def _read_pfs_file(self, filename, encoding, unique_keywords=False):
        try:
            yml = self._pfs2yaml(filename, encoding)
            target_list = parse_yaml_preserving_duplicates(yml, unique_keywords)
        except AttributeError:  # This is the error raised if parsing fails, try again with the normal loader
            target_list = yaml.load(yml, Loader=yaml.CFullLoader)
        except FileNotFoundError as e:
            raise FileNotFoundError(str(e))
        except Exception as e:
            raise ValueError(f"{filename} could not be parsed. " + str(e))
        sections = [PfsSection(list(d.values())[0]) for d in target_list]
        names = [list(d.keys())[0] for d in target_list]
        return names, sections

    @staticmethod
    def _parse_non_file_input(input, names=None):
        """dict/PfsSection or lists of these can be parsed"""
        if names is None:
            assert isinstance(input, Mapping), "input must be a mapping"
            names, sections = PfsDocument._unravel_items(input.items)
            for sec in sections:
                assert isinstance(
                    sec, Mapping
                ), "all targets must be PfsSections/dict (no key-value pairs allowed in the root)"
            return names, sections
        # else:
        #     warnings.warn(
        #         "Creating a PfsDocument with names argument is deprecated, provide instead the names as keys in a dictionary",
        #         FutureWarning,
        #     )

        if isinstance(names, str):
            names = [names]

        if isinstance(input, _PfsBase):
            sections = [input]
        elif isinstance(input, dict):
            sections = [PfsSection(input)]
        elif isinstance(input, (List, Tuple)):
            if isinstance(input[0], _PfsBase):
                sections = input
            elif isinstance(input[0], dict):
                sections = [PfsSection(d) for d in input]
            else:
                raise ValueError("List input must contain either dict or PfsSection")
        else:
            raise ValueError(
                f"Input of type ({type(input)}) could not be parsed (pfs file, dict, PfsSection, lists of dict or PfsSection)"
            )
        if len(names) != len(sections):
            raise ValueError(
                f"Length of names ({len(names)}) does not match length of target sections ({len(sections)})"
            )
        return names, sections

    @property
    def _is_FM_engine(self):
        return "FemEngine" in self.names[0]

    def _add_all_FM_aliases(self) -> None:
        """create MIKE FM module aliases"""
        self._add_FM_alias("HD", "HYDRODYNAMIC_MODULE")
        self._add_FM_alias("SW", "SPECTRAL_WAVE_MODULE")
        self._add_FM_alias("TR", "TRANSPORT_MODULE")
        self._add_FM_alias("MT", "MUD_TRANSPORT_MODULE")
        self._add_FM_alias("EL", "ECOLAB_MODULE")
        self._add_FM_alias("ST", "SAND_TRANSPORT_MODULE")
        self._add_FM_alias("PT", "PARTICLE_TRACKING_MODULE")
        self._add_FM_alias("DA", "DATA_ASSIMILATION_MODULE")

    def _add_FM_alias(self, alias: str, module: str) -> None:
        """Add short-hand alias for MIKE FM module, e.g. SW, but only if active!"""
        if hasattr(self.targets[0], module) and hasattr(
            self.targets[0], "MODULE_SELECTION"
        ):
            mode_name = f"mode_of_{module.lower()}"
            mode_of = int(self.targets[0].MODULE_SELECTION.get(mode_name, 0))
            if mode_of > 0:
                setattr(self, alias, self.targets[0][module])
                self._ALIAS_LIST.append(alias)

    def _pfs2yaml(self, filename, encoding=None) -> str:

        if hasattr(filename, "read"):  # To read in memory strings StringIO
            pfsstring = filename.read()
        else:
            with (open(filename, encoding=encoding)) as f:
                pfsstring = f.read()

        lines = pfsstring.split("\n")

        output = []
        output.append("---")

        _level = 0

        for line in lines:
            adj_line, _level = self._parse_line(line, _level)
            output.append(adj_line)

        return "\n".join(output)

    def _parse_line(self, line: str, level: int = 0) -> str:
        section_header = False
        s = line.strip()
        s = re.sub(r"\s*//.*", "", s)  # remove comments

        if len(s) > 0:
            if s[0] == "[":
                section_header = True
                s = s.replace("[", "")

                # This could be an option to create always create a list to handle multiple identical root elements
                if level == 0:
                    s = f"- {s}"

            if s[-1] == "]":
                s = s.replace("]", ":")

        s = s.replace("//", "")
        s = s.replace("\t", " ")

        if len(s) > 0 and s[0] != "!":
            if "=" in s:
                idx = s.index("=")

                key = s[0:idx]
                key = key.strip()
                value = s[(idx + 1) :].strip()

                if key == "start_time":
                    value = datetime.strptime(value, "%Y, %m, %d, %H, %M, %S").strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                value = self._parse_param(value)
                s = f"{key}: {value}"

        if "EndSect" in line:
            s = ""

        ws = " " * 2 * level
        if level > 0:
            ws = "  " + ws  # TODO
        adj_line = ws + s

        if section_header:
            level += 1
        if "EndSect" in line:
            level -= 1

        return adj_line, level

    def _parse_param(self, value: str) -> str:
        if len(value) == 0:
            return "[]"

        if "," in value:
            tokens = self._split_line_by_comma(value)
            for j in range(len(tokens)):
                tokens[j] = self._parse_token(tokens[j])
            value = f"[{','.join(tokens)}]" if len(tokens) > 1 else tokens[0]
        else:
            value = self._parse_token(value)
        return value

    _COMMA_MATCHER = re.compile(r",(?=(?:[^\"']*[\"'][^\"']*[\"'])*[^\"']*$)")

    def _split_line_by_comma(self, s: str):
        return self._COMMA_MATCHER.split(s)
        # import shlex
        # lexer = shlex.shlex(s)
        # lexer.whitespace += ","
        # lexer.quotes += "|"
        # lexer.wordchars += ",.-"
        # return list(lexer)

    def _parse_token(self, token: str) -> str:
        s = token.strip()

        if s.count("|") == 2:
            parts = s.split("|")
            if len(parts[1]) > 1 and parts[1].count("'") > 0:
                # string containing single quotes that needs escaping
                warnings.warn(
                    f"The string {s} contains a single quote character which will be temporarily converted to \U0001F600 . If you write back to a pfs file again it will be converted back."
                )
                parts[1] = parts[1].replace("'", "\U0001F600")
            s = parts[0] + "'|" + parts[1] + "|'" + parts[2]

        if len(s) > 2:  # ignore foo = ''
            s = s.replace("''", '"')

        return s

    def write(self, filename=None):
        """Write object to a pfs file

        Parameters
        ----------
        filename: str, optional
            Full path and filename of pfs to be created.
            If None, the content will be returned
            as a list of strings.
        """
        from mikeio import __version__ as mikeio_version

        if filename is None:
            return self._to_txt_lines()

        with open(filename, "w") as f:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"// Created     : {now}\n")
            f.write(r"// By          : MIKE IO")
            f.write("\n")
            f.write(rf"// Version     : {mikeio_version}")
            f.write("\n\n")

            self._write_with_func(f.write, level=0)


class Pfs(PfsDocument):
    pass
