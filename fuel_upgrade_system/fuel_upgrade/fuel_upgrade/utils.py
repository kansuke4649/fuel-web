# -*- coding: utf-8 -*-

#    Copyright 2014 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from fnmatch import fnmatch
import functools
import glob
import io
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib2
import uuid

from copy import deepcopy
from distutils.version import StrictVersion

from mako.template import Template
import requests
from six.moves import range
import yaml

from fuel_upgrade import errors

logger = logging.getLogger(__name__)


def exec_cmd(cmd):
    """Execute command with logging.
    Ouput of stdout and stderr will be written
    in log.

    :param cmd: shell command
    """
    logger.debug(u'Execute command "%s"', cmd)
    child = subprocess.Popen(
        cmd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True)

    logger.debug(u'Stdout and stderr of command "%s":', cmd)
    for line in child.stdout:
        logger.debug(line.rstrip())

    _wait_and_check_exit_code(cmd, child)


def safe_exec_cmd(cmd):
    """Execute command with logging.
    Ouput of stdout and stderr will be written
    in log. Doesn't raise error in case
    of non zero exit code.

    :param cmd: shell command
    """
    try:
        exec_cmd(cmd)
    except errors.ExecutedErrorNonZeroExitCode as exc:
        logger.warn(exc)


def exec_cmd_iterator(cmd):
    """Execute command with logging.

    :param cmd: shell command
    :returns: generator where yeach item
              is line from stdout
    """
    logger.debug(u'Execute command "%s"', cmd)
    child = subprocess.Popen(
        cmd, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True)

    logger.debug(u'Stdout and stderr of command "%s":', cmd)
    for line in child.stdout:
        logger.debug(line.rstrip())
        yield line

    _wait_and_check_exit_code(cmd, child)


def _wait_and_check_exit_code(cmd, child):
    """Wait for child and check it's exit code

    :param cmd: command
    :param child: object which returned by subprocess.Popen
    :raises: ExecutedErrorNonZeroExitCode
    """
    child.wait()
    exit_code = child.returncode

    if exit_code != 0:
        raise errors.ExecutedErrorNonZeroExitCode(
            u'Shell command executed with "{0}" '
            'exit code: {1} '.format(exit_code, cmd))

    logger.debug(u'Command "%s" successfully executed', cmd)


def get_request(url):
    """Make http get request and deserializer json response

    :param url: url
    :returns list|dict: deserialized response
    """
    logger.debug('GET request to %s', url)
    response = urllib2.urlopen(url)
    response_data = response.read()
    response_code = response.getcode()
    logger.debug('GET response from %s, code %d, data: %s',
                 url, response_code, response_data)

    return json.loads(response_data), response_code


def topological_sorting(dep_graph):
    """Implementation of topological sorting algorithm
    http://en.wikipedia.org/wiki/Topological_sorting

    :param dep_graph: graph of dependencies, where key is
                      a node and value is a list of dependencies
    :returns: list of nodes
    :raises CyclicDependencies:
    """
    sorted_nodes = []
    graph = deepcopy(dep_graph)

    while graph:
        cyclic = True
        for node, dependencies in sorted(graph.items(), key=lambda n: n[0]):
            for dependency in dependencies:
                if dependency in graph:
                    break
            else:
                cyclic = False
                del graph[node]
                sorted_nodes.append(node)

        if cyclic:
            raise errors.CyclicDependenciesError(
                u'Cyclic dependencies error {0}'.format(graph))

    return sorted_nodes


def create_dir_if_not_exists(dir_path):
    """Creates directory if it doesn't exist

    :param dir_path: directory path
    """
    if not os.path.isdir(dir_path):
        os.makedirs(dir_path)


def render_template_to_file(src, dst, params):
    """Render mako template and write it to specified file

    :param src: path to template
    :param dst: path where rendered template will be saved
    """
    logger.debug('Render template from %s to %s with params: %s',
                 src, dst, params)
    with open(src, 'r') as f:
        template_cfg = f.read()

    with open(dst, 'w') as f:
        rendered_cfg = Template(template_cfg).render(**params)
        f.write(rendered_cfg)


def wait_for_true(check, timeout=60, interval=0.5):
    """Execute command with retries

    :param check: callable object
    :param timeout: timeout
    :returns: result of call method

    :raises TimeoutError:
    """
    start_time = time.time()

    while True:
        result = check()
        if result:
            return result
        if time.time() - start_time > timeout:
            raise errors.TimeoutError(
                'Failed to execute '
                'command with timeout {0}'.format(timeout))
        time.sleep(interval)


def symlink(source, destination, overwrite=True):
    """Creates a symbolic link to the resource.

    :param source: symlink from
    :param destination: symlink to
    :param overwrite: overwrite a destination if True
    """
    logger.debug(
        u'Symlinking "%s" -> "%s" [overwrite=%d]',
        source, destination, overwrite)

    if overwrite or not os.path.exists(destination):
        remove(destination)
        os.symlink(source, destination)
    else:
        logger.debug('Skip symlinking process')


def symlink_if_src_exists(source, destination, overwrite=True):
    """Creates a symbolic link to the resource but only if source exists.

    :param source: symlink from
    :param destination: symlink to
    :param overwrite: overwrite a destination if True
    """
    if not os.path.exists(source):
        logger.debug(
            u'Skip creating symlink, because "%s" does not exists', source)
        return
    symlink(source, destination, overwrite=overwrite)


def hardlink(source, destination, overwrite=True):
    """Creates a hardlink link to the resource.

    :param source: hardlink from
    :param destination: hardlink to
    :param overwrite: overwrite a destination if True
    """
    logger.debug(
        u'Creating hardlink "%s" -> "%s" [overwrite=%d]',
        source, destination, overwrite)

    if overwrite or not os.path.exists(destination):
        remove_if_exists(destination)
        os.link(source, destination)
    else:
        logger.debug('Skip hardlink creation process')


def remove_if_exists(path):
    """Removes files if it exists

    :param path: path to file for removal
    """
    if os.path.exists(path):
        logger.debug(u'Remove file "%s"', path)
        os.remove(path)


def file_contains_lines(file_path, patterns):
    """Checks if file contains lines
    which described by patterns

    :param file_path: path to file
    :param patterns: list of strings
    :returns: True if file matches all patterns
              False if file doesn't match one or more patterns
    """
    logger.debug(
        u'Check if file "%s" matches to pattern "%s"', file_path, patterns)

    regexps = [re.compile(pattern) for pattern in patterns]

    with open(file_path, 'r') as f:
        for line in f:
            for i, regexp in enumerate(regexps):
                result = regexp.search(line)
                if result:
                    del regexps[i]

    if regexps:
        logger.warn('Cannot find lines %s in file %s', regexps, file_path)
        return False

    return True


def copy_if_does_not_exist(from_path, to_path):
    """Copies destination does not exist

    :param from_path: src path
    :param to_path: dst path
    """
    if os.path.exists(to_path):
        logger.debug(
            'Skip file copying, because file %s '
            'already exists', to_path)
        return

    copy(from_path, to_path)


def copy_if_exists(from_path, to_path):
    """Copies destination if it exists

    :param from_path: src path
    :param to_path: dst path
    """
    if not os.path.exists(from_path):
        logger.debug(
            'Skip file copying, because file %s '
            'does not exist', from_path)
        return

    copy(from_path, to_path)


def copy(source, destination, overwrite=True, symlinks=True):
    """Copy a given file or directory from one place to another.

    Both `source` and `destination` should be a path to either file or
    directory. In case `source` is a path to file, the `destination` could
    be a path to directory.

    :param source: copy from
    :param destination: copy to
    :param overwrite: overwrite destination if True
    :param symlinks: resolve symlinks if True
    """
    if os.path.isdir(source):
        copy_dir(source, destination, overwrite, symlinks)
    else:
        copy_file(source, destination, overwrite)


def copy_file(source, destination, overwrite=True):
    """Copy a given source file to a given destination.

    :param source: copy from
    :param destination: copy to
    :param overwrite: overwrite destination if True
    """
    logger.debug(
        u'Copying "%s" -> "%s" [overwrite=%d]',
        source, destination, overwrite)

    # tranform destinatio to path/to/file, not path/to/dir
    if os.path.isdir(destination):
        basename = os.path.basename(source)
        destination = os.path.join(destination, basename)

    # copy only if overwrite is true or destination doesn't exist
    if overwrite or not os.path.exists(destination):
        shutil.copy(source, destination)
    else:
        logger.debug('Skip copying process')


def copy_dir(source, destination, overwrite=True, symlinks=True):
    """Copy a given directory to a given destination.

    :param source: copy from
    :param destination: copy to
    :param overwrite: overwrite destination if True
    :param symlinks: resolve symlinks if True
    """
    logger.debug(
        u'Copying "%s" -> "%s" [overwrite=%d symlinks=%d]',
        source, destination, overwrite, symlinks)

    if overwrite or not os.path.lexists(destination):
        if os.path.lexists(destination):
            remove(destination, ignore_errors=True)

        shutil.copytree(source, destination, symlinks=True)
    else:
        logger.debug('Skip copying process')


def remove(path, ignore_errors=True):
    """Remove a given path, no matter what it is: file or directory.

    :param path: a file or directory to remove
    :param ignore_errors: ignore some errors and non existense if True
    """
    logger.debug(u'Removing "%s"', path)

    if ignore_errors and not os.path.lexists(path):
        return

    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=ignore_errors)
    else:
        os.remove(path)


def rmtree(source, ignore_errors=True):
    """Remove directory

    :param str source: path to directory
    :param bool ignore_errors: ignores error if True
    """
    logger.debug(u'Removing %s', source)
    if os.path.exists(source):
        shutil.rmtree(source, ignore_errors=ignore_errors)


def rename(source, destination, overwrite=True):
    """Rename some source into a given destination.

    In Unix terms, it's a move operation.

    :param str source: a source to be renamed
    :param str destination: rename to
    """
    logger.debug(
        u'Renaming "%s" -> "%s" [overwrite=%d]',
        source, destination, overwrite)

    if overwrite or not os.path.exists(destination):
        os.rename(source, destination)


def dict_merge(a, b):
    """Recursively merges two given dictionaries.

    :param a: a first dict
    :param b: a second dict
    :returns: a result dict (merge result of a and b)
    """
    if not isinstance(b, dict):
        return deepcopy(b)
    result = deepcopy(a)
    for k, v in b.iteritems():
        if k in result and isinstance(result[k], dict):
            result[k] = dict_merge(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result


def load_fixture(fileobj, loader=None):
    """Loads a fixture from a given `fileobj` and process it with
    our extended markup that provides an inherit feature.

    :param fileobj: a file-like object with fixture
    :para, loader: a fixture loader; use default one if None
    """
    # a key that's used to mark some item as abstract
    pk_key = 'pk'

    # a key that's used to tell some item inherit data
    # from an abstract one
    inherit_key = 'extend'

    # a list of supported loaders; the loader should be a func
    # that receives a file-like object
    supported_loaders = {
        '.json': json.load,
        '.yaml': yaml.load,
        '.yml': yaml.load,
    }

    def extend(obj):
        if inherit_key in obj:
            obj[inherit_key] = extend(obj[inherit_key])
        return dict_merge(obj.get(inherit_key, {}), obj)

    # try to get loader from a given fixture if loader is None
    if loader is None:
        _, ext = os.path.splitext(fileobj.name)
        loader = supported_loaders[ext]
    fixture = loader(fileobj)

    # render fixture
    fixture = filter(lambda obj: obj.get(pk_key) is not None, fixture)
    for i in range(0, len(fixture)):
        fixture[i] = extend(fixture[i])
        fixture[i].pop(inherit_key, None)

    return [f['fields'] for f in fixture]


def check_file_is_valid_json(path):
    """Checks if file contains valid json

    :param str path: path to json file
    :returns: True if valid False if invalid
    """
    try:
        json.load(open(path, 'r'))
    except (ValueError, IOError):
        return False

    return True


def calculate_free_space(path):
    """Calculate free space

    :param str path: path to directory for free space calculation
    :returns: free space in megabytes
    """
    # NOTE(eli): to calculate the size of mount point
    # need to add `/` symbol at the end of the path
    directory = '{0}/'.format(path)
    device_info = os.statvfs(directory)
    return byte_to_megabyte(device_info.f_bsize * device_info.f_bavail)


def byte_to_megabyte(byte):
    """Convert bytes to megabytes

    :param byte: quantity of bytes
    :returns: megabytes
    """
    return byte / 1024 ** 2


def find_mount_point(path):
    """Tries to find mount point of directory

    :param str path: path to
    :returns: path to mount point
    """
    path = os.path.abspath(path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)

    return path


def files_size(files_list):
    """Returns size of files

    :param list path: list of files
    :returns: sum of files sizes
    """
    size = sum(
        os.path.getsize(f) for f in files_list if os.path.isfile(f))
    return byte_to_megabyte(size)


def dir_size(path):
    """Returns size of file or directory

    :param str path: path to the directory
    :returns: size of the directory
    """
    total_size = 0
    for dirpath, _, filenames in os.walk(path, followlinks=True):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)

    return byte_to_megabyte(total_size)


def compare_version(v1, v2):
    """Compare two versions

    :param str v1: version 1
    :param str v2: version 2
    :returns: 0 - versions are equal
              -1 - version 1 is higher than version 2
               1 - version 2 is higher than version 1
    """
    version1 = StrictVersion(v1)
    version2 = StrictVersion(v2)

    if version1 == version2:
        return 0
    elif version1 > version2:
        return -1
    else:
        return 1


def get_required_size_for_actions(actions, update_path):
    """Returns a size on disk that will be required for completing
    a given actions list.

    :param actions: a list of actions
    :returns: a size
    """
    rv = {}

    for action in actions:
        # copy / copy_from_update case
        if action['name'] == 'copy':
            src = action['from']

            dst = action['to']
            if not os.path.isdir(dst):
                dst = os.path.dirname(dst)

            if dst not in rv:
                rv[dst] = 0

            if os.path.isdir(src):
                rv[dst] += dir_size(src)
            else:
                rv[dst] += files_size(src)

    return rv


def save_as_yaml(path, data):
    """Saves data as yaml data structure in file

    :param str path: path to file to save data
    :param data: data to save as yaml
    """
    logger.debug('Update file %s with data %s', path, data)

    with open(path, 'w') as f:
        astute_str = yaml.dump(data, default_flow_style=False)
        f.write(astute_str)


def read_from_yaml(path):
    """Opens file, reads data from it and deserializes it from yaml

    :param str path: path to file
    """
    with io.open(path, 'r', encoding='utf-8') as f:
        data = yaml.load(f.read())

    logger.debug('Read data %s from file %s', data, file)
    return data


def generate_uuid_string():
    """Generates uuid string

    :returns: generated uuid
    """
    return str(uuid.uuid4())


def verify_postgres_dump(pg_dump_path):
    """Checks that postgresql dump is correct

    :param str pg_dump_path: path to postgresql dump
    """
    if not os.path.exists(pg_dump_path):
        return False

    patterns = [
        '-- PostgreSQL database cluster dump',
        '-- PostgreSQL database dump',
        '-- PostgreSQL database dump complete',
        '-- PostgreSQL database cluster dump complete']

    return file_contains_lines(pg_dump_path, patterns)


def file_extension(file_path):
    """Retrieves extension from file name

    :param str file_path: path to the file or file name
    :returns: file's extension
    """
    _, ext = os.path.splitext(file_path)
    return ext[1:]


def file_exists(file_path):
    """Checks if file exists

    :param str file_path: path to the file
    :returns: True if file exists
              False id doesn't
    """
    return os.path.exists(file_path)


def iterfiles(path):
    """Iterate over all files in the ``path`` directory.

    :param path: a path to find in
    """
    for root, dirnames, filenames in os.walk(path, topdown=True):
        for filename in filenames:
            yield os.path.join(root, filename)


def extract_env_version(release_version):
    """Returns environment version based on release version.

    A release version consists of 'OSt' and 'MOS' versions: '2014.1.1-5.0.2'
    so we need to extract 'MOS' version and returns it as result.

    :param release_version: a string which represents a release version
    :returns: an environment version
    """
    separator = '-'

    # unfortunately, Fuel 5.0 didn't has an env version in release_version
    # so we need to handle that special case
    if release_version == '2014.1':
        return '5.0'

    # we need to extract a second part since it's what we're looking for
    return release_version.split(separator)[1]


def sanitize(obj, keywords, mask='******'):
    """Find and hide private data in obj using keywords.

    :param obj: object to be sanitized
    :param keywords: describe keywords to be found in obj
    :param mask: a string for substitution of sanitized values
    :return: sanitized copy of obj
    """
    def _helper(_obj):
        if isinstance(_obj, dict):
            for option in _obj:
                if any([key in option for key in keywords]):
                    _obj[option] = mask
                else:
                    _helper(_obj[option])

        elif isinstance(_obj, (list, set, tuple)):
            for value in _obj:
                _helper(value)

        return _obj

    # Making sure the original object remains untouched
    obj_copy = deepcopy(obj)
    return _helper(obj_copy)


def iterfiles_filter(dir_path, file_pattern):
    """Returns generator where each item is a path to file, that satisfies
    file_patterns condtion

    :param dir_path: path to directory, e.g /etc/puppet/
    :param file_pattern: unix filepattern to match files
    """
    for file_path in iterfiles(dir_path):
        if fnmatch(file_path, file_pattern):
            yield file_path


def normversion(version):
    """Normalize a given version to have exactly three components.

    :param version: a version to be normalized
    :returns: a normalized version
    """
    components = version.split('.')

    if len(components) < 3:
        for _ in range(0, 3 - len(components)):
            components.append('0')

    return '.'.join(components)


class VersionedFile(object):
    """Set of methods for versioned files.
    If `basename` is '/tmp/file.ext' it allows
    to get and filter list of files with names
    '/tmp/file.ext.N' where N is integer.

    :param str basename: prefix for versioned files
    """

    def __init__(self, basename):
        #: prefix for all versioned files
        self.basename = basename
        self._pattern = '{0}.{{0}}'.format(self.basename)

    def next_file_name(self):
        """Returns free file name
        If directory has file '/tmp/file.ext.10'
        method returns file name '/tmp/file.ext.11'.
        If it does not have any files it returns
        file name '/tmp/file.ext.0', where '/tmp/file.ext'
        is example of `basename`

        :returns: file name
        """
        return self._pattern.format(self._get_last_number() + 1)

    def sorted_files(self):
        """Files sorted by extension

        :returns: list of sorted by extension files
        """
        return sorted(
            self._files_with_numeric_extension(),
            key=lambda f: int(file_extension(f)),
            reverse=True)

    def _get_last_number(self):
        """Retrieves last number from file name

        :returns: last file number, if there is no files, returns 0
        """
        files = self.sorted_files()
        if not files:
            return 0

        return int(file_extension(files[0]))

    def _files_with_numeric_extension(self):
        """Fiels with numeric extension

        :returns: files which have digit extension
        """
        return filter(
            lambda f: file_extension(f).isdigit(),
            glob.glob(self._pattern.format('*')))


class http_retry(object):
    """Retry entire method if it raises HTTP error with specific status code.

    :param status_codes: retry if one of the status codes was raised
    :param count: a number of attempts
    :param interval: an interval between attempts in seconds
    """

    def __init__(self, status_codes, attempts=3, interval=2):
        self._status_codes = status_codes
        self._attempts = attempts
        self._interval = interval

    def __call__(self, fn):
        @functools.wraps(fn)
        def _wrapped(*args, **kwargs):
            for attempt in range(1, self._attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except requests.HTTPError as exc:
                    logger.exception(
                        'HTTP request ends with %d (attempt %d/%d)',
                        exc.response.status_code, attempt, self._attempts)

                    # we should stop perform retries if
                    #   - status_code is not interesting for us
                    #   - it's the last attempt
                    if any([
                        exc.response.status_code not in self._status_codes,
                        attempt == self._attempts,
                    ]):
                        raise

                time.sleep(self._interval)

        return _wrapped
