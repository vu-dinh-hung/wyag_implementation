import argparse
from collections import OrderedDict
import configparser
import hashlib
import os
import re
import sys
from typing import Optional, OrderedDict
import zlib

parser = argparse.ArgumentParser(description='The stupid content tracker')
subparsers = parser.add_subparsers(title='Commands', dest='command')
subparsers.required = True

# init
subparser = subparsers.add_parser('init', help='Initialize a new, empty repository')
subparser.add_argument('path', metavar='directory', nargs='?', default='.', help='Where to create the repository')

# cat-file
subparser = subparsers.add_parser('cat-file', help='Provide content of repository objects')
subparser.add_argument('type', metavar='type', choices=['blob', 'commit', 'tag', 'tree'], help='Specify the type')
subparser.add_argument('object', metavar='object', help='The object to display')

# hash-object
subparser = subparsers.add_parser('hash-object', help='Compute object ID and optionally creates a blob from a file')
subparser.add_argument('path', help='Read object from <file>')
subparser.add_argument('-t', metavar='type', dest='type', choices=['blob', 'commit', 'tag', 'tree'], default='blob', help='Specify the object type')
subparser.add_argument('-w', metavar='write', dest='write', action='store_true', help='Write the object to disk')


# log
subparser = subparsers.add_parser('log', help='Display history of a given commit')
subparser.add_argument('commit', default='HEAD', nargs='?', help='Commit to start at')


def get_repo_path(repo, *path):
    return os.path.join(repo.gitdir, *path)


def get_repo_file_path(repo, *path, mkdir=False):
    if get_repo_dir_path(repo, *path[:-1], mkdir=mkdir):
        return get_repo_path(repo, *path)


def get_repo_dir_path(repo, *path, mkdir=False):
    path = get_repo_path(repo, *path)

    if os.path.exists(path):
        if os.path.isdir(path):
            return path
        raise Exception(f'Not a directory {path}')

    if mkdir:
        os.makedirs(path)
        return path


class Repository:
    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, '.git')

        if not force and not os.path.isdir(self.gitdir):
            raise Exception(f'Not a git repository {self.gitdir}')

        # Read config
        self.config = configparser.ConfigParser()
        cf_path = get_repo_file_path(self, 'config')
        print(f'{cf_path=}')

        if cf_path and os.path.exists(cf_path):
            self.config.read(cf_path)
        elif not force:
            raise Exception('Config file missing')

        if not force:
            version = int(self.config.get('core', 'repositoryformatversion'))
            if version != 0:
                raise Exception('Unsupported repositoryformatversion {vers}')


def get_repo_default_config():
    config = configparser.ConfigParser()

    config.add_section('core')
    config.set('core', 'repositoryformatversion', '0')
    config.set('core', 'filemode', 'false')
    config.set('core', 'bare', 'false')

    return config


def create_repo(path) -> Repository:
    repo = Repository(path, True)

    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception(f'{path} is not a directory!')
        if os.listdir(repo.worktree):
            raise Exception(f'{path} is not empty!')
    else:
        os.makedirs(repo.worktree)

    # .git/branches/
    assert get_repo_dir_path(repo, 'branches', mkdir=True)

    # .git/objects/
    assert get_repo_dir_path(repo, 'objects', mkdir=True)

    # .git/refs/tags/ & .git/refs/heads/
    assert get_repo_dir_path(repo, 'refs', 'tags', mkdir=True)
    assert get_repo_dir_path(repo, 'refs', 'heads', mkdir=True)

    # .git/description
    with open(get_repo_file_path(repo, 'description'), 'w') as f:
        f.write('Unnamed repository; edit this file \'description\' to name the repository.\n')

    # .git/HEAD
    with open(get_repo_file_path(repo, 'HEAD'), 'w') as f:
        f.write('ref: refs/heads/main\n')

    # .git/config
    with open(get_repo_file_path(repo, 'config'), 'w') as f:
        default_config = get_repo_default_config()
        default_config.write(f)

    return repo


def cmd_init(args):
    create_repo(args.path)


def get_repo(path='.', required=True) -> Optional[Repository]:
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, '.git')):
        return Repository()

    # recurse in parent path
    parent = os.path.realpath(os.path.join(path, '..'))

    if parent == path:  # if at root
        if required:
            raise Exception('No git directory.')
        else:
            return None

    return get_repo(parent, required)


class GitObject:
    def __init__(self, repo, data=None):
        self.repo = repo

        if data != None:
            self.deserialize(data)

        def serialize(self):
            """This needs to be implemented by subclasses"""
            raise Exception("Unimplemented!")

        def deserialize(self):
            raise Exception("Unimplemented")


class GitBlob(GitObject):
    type = b'blob'

    def serialize(self):
        return self.blobdata

    def deserialize(self, data):
        self.blobdata = data


def get_object_name(repo, name, type=None, folow=True):
    return name


def read_object(repo, sha):
    path = get_repo_file_path(repo, 'objects', sha[:2], sha[2:])

    with open(path, 'rb') as f:
        raw = zlib.decompress(f.read())

        space_pos = raw.find(b' ')
        null_pos = raw.find(b'\x00')

        # get object type
        object_type = raw[:space_pos]

        # validate size
        size = int(raw[space_pos+1:null_pos].decode('ascii'))
        if size != len(raw) - 1 - null_pos:
            raise Exception(f'Malformed object {sha}: bad length')

        # get object class
        if object_type == b'commit':    c = GitCommit
        elif object_type == b'tree':    c = GitTree
        elif object_type == b'tag':     c = GitTag
        elif object_type == b'blob':    c = GitBlob
        else:
            raise Exception(f'Unknown type {object_type.decode("ascii")} for object {sha}')

    return c(repo, raw[null_pos+1:])


def write_object(obj, to_disk=True):
    # serialize
    data = obj.serialize()

    # header
    result = obj.type + b' ' + str(len(data)).encode() + b'\x00' + data

    # hash
    sha = hashlib.sha1(result).hexdigest()

    if to_disk:
        path = get_repo_file_path(obj.repo, 'objects', sha[0:2], sha[2:], mkdir=True)

        with open(path, 'wb') as f:
            f.write(zlib.compress(result))

    return sha


def cmd_cat_file(args):
    repo = get_repo()
    cat_file(repo, args.object, type=args.type.encode())


def cat_file(repo, obj, type=None):
    obj = read_object(repo, get_object_name(repo, obj, type))
    sys.stdout.buffer.write(obj.serialize())


def cmd_hash_object(args):
    repo = None
    if args.write:
        repo = Repository('.')

    with open(args.path, 'rb') as fd:
        sha = hash_object(fd, args.type.encode(), repo)
        print(sha)


def hash_object(fd, type, repo=None):
    data = fd.read()

    if type == b'commit':   obj = GitCommit(repo, data)
    elif type == b'tree':   obj = GitTree(repo, data)
    elif type == b'tag':    obj = GitTag(repo, data)
    elif type == b'tag':    obj = GitBlob(repo, data)
    else:
        raise Exception(f'Unknown type {type}')

    return write_object(obj, to_disk=repo)


def parse_commit_format(raw, start=0, dct=None) -> OrderedDict:
    dct = dct or OrderedDict()

    space_pos = raw.find(b' ', start)
    newline_pos = raw.find(b'\n', start)

    # Base case:
    # if newline appears before space, the rest of the data is the message
    if (space_pos < 0) or (newline_pos < space_pos):
        assert newline_pos == start
        dct[b''] = raw[start+1:]
        return dct

    # Recursive case:
    key = raw[start:space_pos]

    # end of the current value is where there is a newline not followed by a space
    end = start
    while True:
        end = raw.find(b'\n', end + 1)
        if raw[end+1] != ord(' '):
            break

    value = raw[space_pos+1:end].replace(b'\n ', b'\n')

    if key in dct:
        if isinstance(dct[key], list):
            dct[key].append(value)
        else:
            dct[key] = [dct[key], value]
    else:
        dct[key] = value

    return parse_commit_format(raw, start=end+1, dct=dct)


def serialize_commit_format(commit_dict: OrderedDict):
    result = b''

    # key-value pairs
    for key in commit_dict:
        # skip the message
        if key == b'': continue
        value_list = commit_dict[key]
        if not isinstance(value_list, list):
            value_list = [value_list]

        for value in value_list:
            result += key + b' ' + (value.replace(b'\n', b'\n ')) + b'\n'

    # message
    result += b'\n' + commit_dict[b'']

    return result


class GitCommit(GitObject):
    type = b'commit'

    def deserialize(self, data):
        self.commit_dict = parse_commit_format(data)

    def serialize(self):
        return serialize_commit_format(self.commit_dict)


def cmd_log(args):
    repo = get_repo()

    print('digraph wyaglog{')
    log_graphviz(repo, get_object_name(repo, args.commit), set())
    print('}')


def log_graphviz(repo, sha, seen):
    if sha in seen:
        return
    seen.add(sha)

    commit = read_object(repo, sha)
    assert commit.type == b'commit'

    if not b'parent' in commit.commit_dict:
        return

    parents = commit.commit_dict[b'parent']

    if not isinstance(parents, list):
        parents = [parents]

    for p in parents:
        print(f'c_{sha} -> c_{p.decode("ascii")};')
        log_graphviz(repo, p, seen)


def main(argv=sys.argv[1:]):
    args = parser.parse_args(argv)

    if args.command == 'init':          cmd_init(args)
    elif args.command == 'hash-object': cmd_hash_object(args)
    elif args.command == 'cat-file':    cmd_cat_file(args)
    # elif args.command == 'add':         cmd_add(args)
    # elif args.command == 'rm':          cmd_rm(args)
    # elif args.command == 'commit':      cmd_commit(args)
    # elif args.command == 'checkout':    cmd_checkout(args)
    # elif args.command == 'log':         cmd_log(args)
    # elif args.command == 'ls-tree':     cmd_ls_tree(args)
    # elif args.command == 'merge':       cmd_merge(args)
    # elif args.command == 'rebase':      cmd_rebase(args)
    # elif args.command == 'rev-parse':   cmd_rev_parse(args)
    # elif args.command == 'show-ref':    cmd_show_ref(args)
    # elif args.command == 'tag':         cmd_tag(args)
    # else: raise Exception('Non-existent command')


if __name__ == '__main__':
    create_repo(os.path.join(os.getcwd(), 'test'))
