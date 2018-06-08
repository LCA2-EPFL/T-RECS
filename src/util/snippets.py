"""Utility functions.
"""

import json
import pickle


def load_json_file(json_path, logger=None, encoding=None):
    """Load a JSON file, possibly logging errors.

    Parameters
    ----------
        json_path : path_like
            Relative path to the JSON file.

        logger : logging.Logger (optional, default None)
            Logger to use.

        encoding : str (optional, default None)
            Encoding to use.  None means system preferred encoding.

    Returns
    -------
        contents : dict
            Contents of the JSON file.

    """
    try:
        with open(json_path, 'r', encoding=encoding) as json_file:
            contents = json.load(json_file)
    except OSError as e:
        if logger is not None:
            logger.error("Could not open {}: {}".format(json_path, e))
        raise
    except ValueError as e:
        if logger is not None:
            logger.error("Could not load {}: {}".format(json_path, e))
        raise

    return contents


def load_json_data(json_data, encoding='utf-8'):
    """Load JSON contents from binary data.

    Parameters
    ----------
        json_data : bytes
            Binary data encoding JSON contents.

        encoding : str (optional, default 'utf-8')
            Encoding that was used.

    Returns
    -------
        contents : dict
            JSON contents.

    """
    return json.loads(json_data.decode(encoding))


def dump_json_data(contents, encoding='utf-8'):
    """Dump JSON contents to binary.

    Parameters
    ----------
        contents : dict
            Contents to dump.

        encoding : str (optional, default 'utf-8')
            Encoding to use.

    Returns
    -------
        json_data : bytes
            JSON contents encoded to binary.

    """
    return json.dumps(contents).encode(encoding)


def load_api(api_path, check_readiness=True):
    """Load a GridAPI instance, possibly waiting until it becomes ready.

    Parameters
    ----------
        api_path : path_line
            Path where the instance is pickled.

        check_readiness : bool (optional, default True)
            Whether to check if the GridAPI instance is ready.

    Returns
    -------
        api : GridAPI
            GridAPI instance such that api.ready() is True.

    """
    while True:
        try:
            with open(api_path, 'rb') as api_file:
                api = pickle.load(api_file)
            if not check_readiness or api.ready():
                break
        except:
            continue

    return api


def dump_api(api, api_path):
    """Dump a GridAPI instance.

    Parameters
    ----------
        api : GridAPI
            Instance to dump.

        api_path : path_like
            Path to which to dump the instance.

    """
    with open(api_path, 'wb') as api_file:
        pickle.dump(api, api_file)
