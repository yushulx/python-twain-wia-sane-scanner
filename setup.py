from setuptools.command import build_ext
from setuptools import setup
import os
import io
from setuptools.command.install import install
import shutil

long_description = io.open("README.md", encoding="utf-8").read()


def copyfiles(src, dst):
    if os.path.isdir(src):
        filelist = os.listdir(src)
        for file in filelist:
            libpath = os.path.join(src, file)
            shutil.copy2(libpath, dst)
    else:
        shutil.copy2(src, dst)


class CustomBuildExt(build_ext.build_ext):
    def run(self):
        build_ext.build_ext.run(self)


class CustomBuildExtDev(build_ext.build_ext):
    def run(self):
        build_ext.build_ext.run(self)


class CustomInstall(install):
    def run(self):
        install.run(self)


setup(name='twain-wia-sane-scanner',
      version='2.0.2',
      description='A Python package for digitizing documents from TWAIN, WIA, SANE, ICA and eSCL compatible scanners.',
      long_description=long_description,
      long_description_content_type="text/markdown",
      author='yushulx',
      url='https://github.com/yushulx/twain-wia-sane-scanner',
      license='MIT',
      packages=['dynamsoftservice'],
      classifiers=[
           "Development Status :: 5 - Production/Stable",
           "Environment :: Console",
           "Intended Audience :: Developers",
          "Intended Audience :: Education",
          "Intended Audience :: Information Technology",
          "Intended Audience :: Science/Research",
          "License :: OSI Approved :: MIT License",
          "Operating System :: Microsoft :: Windows",
          "Operating System :: MacOS",
          "Operating System :: POSIX :: Linux",
          "Programming Language :: Python",
          "Programming Language :: Python :: 3",
          "Programming Language :: Python :: 3 :: Only",
          "Programming Language :: Python :: 3.6",
          "Programming Language :: Python :: 3.7",
          "Programming Language :: Python :: 3.8",
          "Programming Language :: Python :: 3.9",
          "Programming Language :: Python :: 3.10",
          "Topic :: Scientific/Engineering",
          "Topic :: Software Development",
      ],
      install_requires=['requests'],
      cmdclass={
          'install': CustomInstall,
          'build_ext': CustomBuildExt,
          'develop': CustomBuildExtDev},
      )
