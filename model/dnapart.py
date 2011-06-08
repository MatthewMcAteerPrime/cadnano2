# The MIT License
#
# Copyright (c) 2011 Wyss Institute at Harvard University
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
# http://www.opensource.org/licenses/mit-license.php
from exceptions import NotImplementedError
import json
from .part import Part
from .virtualhelix import VirtualHelix
from .enum import LatticeType
from PyQt4.QtCore import pyqtSignal, QObject
from PyQt4.QtGui import QUndoCommand
from util import *
from heapq import *
import copy

class DNAPart(Part):
    """
    Emits:
        dimensionsWillChange(newNumRows, newNumCols, newNumBases)
        virtualHelixAtCoordsChanged(row, col)  # the VH at row, col will
            * change its idnum (the dnapart owns the idnum)
            * change its virtualhelix object (maybe from or to None)
        selectionWillChange()
    """
    selectAllBehavior = True  # Always select all helices in part
    def __init__(self, *args, **kwargs):
        if self.__class__ == DNAPart:
            raise NotImplementedError("This class is abstract. Perhaps you want DNAHoneycombPart.")
        super(DNAPart, self).__init__(self, *args, **kwargs)
        self._numberToVirtualHelix = {}  # number -> VirtualHelix
        self._name = kwargs.get('name', 'untitled')
        self._maxRow = kwargs.get('maxRow', 20)
        self._maxCol = kwargs.get('maxCol', 20)
        # ID assignment infra
        self.oddRecycleBin, self.evenRecycleBin = [], []
        self.reserveBin = set()
        self.highestUsedOdd = -1  # Used iff the recycle bin is empty and highestUsedOdd+2 is not in the reserve bin
        self.highestUsedEven = -2  # same
        # Transient and/or cached state
        self._staples = []
        self._scaffolds = []
        self._selection = list()
        self._coordToVirtualHelix = {}  # (row,col) -> VirtualHelix
        # Abstract
        # self._maxBase = 0  # honeycomb is 42
        # self._activeSlice = 0  # honeycomb is 21
        if self.selectAllBehavior:
            self.virtualHelixAtCoordsChanged.connect(self.updateSelectionFromVHChange)
    
    def setDocument(self, newDoc):
        """Only called by Document"""
        super(DNAPart, self).setDocument(newDoc)
        ctrlr = newDoc.controller()
        if ctrlr:
            self.dimensionsWillChange.connect(ctrlr.dirty)
            self.virtualHelixAtCoordsChanged.connect(ctrlr.dirty)
    
    def dimensions(self):
        return (self._maxRow, self._maxCol, self._maxBase)
    
    def numBases(self):
        return self._maxBase
    
    dimensionsWillChange = pyqtSignal()
    def setDimensions(self, newDim):
        self.dimensionsWillChange.emit(newDim)
        self._maxRow, self._maxCol, self._maxBase = newDim
        for n in self._numberToVirtualHelix:
            self._numberToVirtualHelix[n].setNumBases(self._maxBase)
        
    ############################# Archiving/Unarchiving #############################
    def fillSimpleRep(self, sr):
        """
        Provides a representation of the receiver in terms of simple
        (container,atomic) classes and other objects implementing simpleRep
        """
        sr['.class'] = "DNAPart"
        # JSON doesn't like keys that aren't strings, so we cheat and use an array
        # Entries look like ((row,col),num,vh)
        coordsAndNumToVH = []
        for vh in self._coordToVirtualHelix.itervalues():
            coordsAndNumToVH.append((vh.coord(), vh.number(), vh))
        sr['virtualHelices'] = coordsAndNumToVH
        sr['name'] = self._name
    
    # First objects that are being unarchived are sent
    # ClassNameFrom.classAttribute(incompleteArchivedDict)
    # which has only strings and numbers in its dict and then,
    # sometime later (with ascending finishInitPriority) they get
    # finishInitWithArchivedDict, this time with all entries
    finishInitPriority = 0.0
    def finishInitWithArchivedDict(self, completeArchivedDict):
        for coord, num, vh in completeArchivedDict['virtualHelices']:
            if num % 2:
                self.highestUsedOdd = max(self.highestUsedOdd, num)
            else:
                self.highestUsedEven = max(self.highestUsedEven, num)
            self.addVirtualHelixAt(coord, vh, requestSpecificIdnum=num, noUndo=True)
        self._name = completeArchivedDict['name']
            
    ############################# VirtualHelix CRUD #############################
    # Take note: vhrefs are the shiny new way to talk to dnapart about its constituent
    # virtualhelices. Wherever you see f(...,vhref,...) you can
    # f(...,27,...)         use the virtualhelix's id number
    # f(...,vh,...)         use an actual virtualhelix
    # f(...,(1,42),...)     use the coordinate representation of its position
    def getVirtualHelix(self, vhref, returnNoneIfAbsent=True):
        """A vhref is the number of a virtual helix, the (row, col) of a virtual helix,
        or the virtual helix itself. For conveniece, CRUD should now work with any of them."""
        vh = None
        if type(vhref) in (int, long):
            vh = self._numberToVirtualHelix.get(vhref, None)
        elif type(vhref) in (tuple, list):
            vh = self._coordToVirtualHelix.get(vhref, None)
        else:
            vh = vhref
        if not isinstance(vh, VirtualHelix):
            if returnNoneIfAbsent:
                return None
            else:
                err = "Couldn't find the virtual helix in part %s "+\
                      "referenced by index %s" % (self, vhref)
                raise IndexError(err)
        return vh

    def getVirtualHelices(self):
        return (self._numberToVirtualHelix[n] for n in self._numberToVirtualHelix)

    virtualHelixAtCoordsChanged = pyqtSignal(int, int)
    def addVirtualHelixAt(self, coords, vh, requestSpecificIdnum=None, noUndo=False):
        c = self.AddHelixCommand(self, coords, vh, requestSpecificIdnum)
        if noUndo:
            c.redo()
        else:
            self.undoStack().push(c)

    def renumber(self, phg):
        """
        """
        print "dope"
        vhs = phg.displayedVHs()
        even_count = 0
        odd_count = 1
        oldNumbers = []
        newNumbers = []
        for vh in vhs:
            oldNumbers.append(vh.number())
            if vh.number() % 2: # is odd number ? 
                newNumbers.append(odd_count)
                odd_count += 2
            else: # it's an even number
                newNumbers.append(even_count)
                even_count += 2
        # end for
        print "oldnums", oldNumbers
        print "newnums",newNumbers
        c = self.RenumberHelicesCommand(self, oldNumbers, newNumbers)
        self.undoStack().push(c)
    # end def

    # emits virtualHelixAtCoordsChanged
    def renumberVirtualHelix(self, vhref, newNumber, automaticallyRenumberConflictingHelix=False):
        vh = getVirtualHelix(vhref, returnNoneIfAbsent = False)
        helixAlreadyAtNewNum = self.getVirtualHelix(newNumber)
        if helixAlreadyAtNewNum:
            if automaticallyRenumberConflictingHelix:
                n = self.reserveHelixIDNumber(parityEven=(newNumber%2==0))
                self.recycleHelixIDNumber(n)
                self.renumberVirtualHelix(helixAlreadyAtNewNum, n)
            else:
                assert(False)  # Tried to assign an idnum belonging to another helix to vhref
        c = self.RenumberHelixCommand(self, vh.coords(), newNumber)

    def getVirtualHelixCount(self):
        """docstring for getVirtualHelixList"""
        return len(self._numberToVirtualHelix)

    def getVirtualHelices(self):
        return [self._numberToVirtualHelix[n] for n in self._numberToVirtualHelix]

    ############################# VirtualHelix Private CRUD #############################
    class AddHelixCommand(QUndoCommand):
        """
        Adds a helix to dnapart. Called by self.addVirtualHelixAt().
        """
        def __init__(self, dnapart, coords, vhelix, requestSpecificIdnum=None):
            super(DNAPart.AddHelixCommand, self).__init__()
            self._part = dnapart
            self._coords = coords  # row, col
            self._parity = self._part.coordinateParityEven(coords)
            self._vhelix = vhelix
            self._requestedNum = requestSpecificIdnum

        def redo(self, actuallyUndo=False):
            if self._vhelix:
                newID = self._part.reserveHelixIDNumber(
                                            parityEven=self._parity,\
                                            requestedIDnum=self._requestedNum)
                self._vhelix._setPart(self._part, self._coords, newID)
                self._part._numberToVirtualHelix[newID] = self._vhelix
                self._part._coordToVirtualHelix[self._coords] = self._vhelix
            self._part.virtualHelixAtCoordsChanged.emit(self._coords[0],\
                                                        self._coords[1])

        def undo(self):
            vh = self._part.getVirtualHelix(self._coords)
            if vh:
                del self._part._coordToVirtualHelix[vh.coord()]
                del self._part._numberToVirtualHelix[vh.number()]
                self._part.recycleHelixIDNumber(vh.number())
            self._part.virtualHelixAtCoordsChanged.emit(self._coords[0],\
                                                        self._coords[1])


    class RenumberHelixCommand(QUndoCommand):
        def __init__(self, dnapart, coords, newNumber):
            super(DNAPart.RenumberHelixCommand, self).__init__()
            self.coords = coords
            self.part = dnapart
            self.newNum = newNumber
            
        def redo(self, actuallyUndo=False):
            p = self.part
            vh = p.getVirtualHelix(self.coords, returnNoneIfAbsent = False)
            currentNum = vh.number()
            if actuallyUndo:
                newNum = self.oldNum
            else:
                self.oldNum = currentNum
            assert(not p.getVirtualHelix(newNum))
            del p._numberToVirtualHelix[currentNum]
            p._numberToVirtualHelix[newNum] = vh
            vh._setNumber(newNum)
            # dnapart owns the idnum of a virtualhelix, so we
            # are the ones that send an update when it changes
            p.virtualHelixAtCoordsChanged.emit(self.row, self.col)
        def undo(self):
            self.redo(actuallyUndo=True)
    
    
    class RenumberHelicesCommand(QUndoCommand):
        """
        This command undoably renumbers a list of vhs.  It can undo/redo 
        all renumbering in one command
        """
        def __init__(self, dnapart, oldNumbers, newNumbers):
            super(DNAPart.RenumberHelicesCommand, self).__init__()
            self._dnapart = dnapart
            # self._phg = phg
            self._oldNumbers = oldNumbers
            self._newNumbers = newNumbers
            assert(len(self._oldNumbers) == len(self._newNumbers))
            
        def redo(self):
            p = self._dnapart
            # create a shallow copy of the dictionary to allow for swapping
            dictcopy = copy.copy(p._numberToVirtualHelix)
            # new_list = []
            for i in range(len(self._oldNumbers)):
                oldNum = self._oldNumbers[i]
                newNum = self._newNumbers[i]
                print oldNum, newNum
                # new_list.append(self._phg.getPathHelix(vh))
                if oldNum != newNum:
                    vh = p._numberToVirtualHelix[oldNum]
                    # if the newnumber is already being used 
                    # swap the pair.
                    dictcopy[newNum] = vh
                    vh.setNumber(newNum)
                    if oldNum not in self._newNumbers:
                        del dictcopy[oldNum]
                    # dnapart owns the idnum of a virtualhelix, so we
                    # are the ones that send an update when it changes
                    # p.virtualHelixAtCoordsChanged.emit(vh.row(), vh.col())
                # end if
            #end for
            print "oldkeys",p._numberToVirtualHelix.keys()
            print "newkeys",dictcopy.keys()
            p._numberToVirtualHelix = dictcopy

            # print  [x.number()  for x in new_list]
            # self._phg.displayedVHsChanged.emit()
            
        def undo(self):
            p = self._dnapart
            # create a shallow copy of the dictionary to allow for swapping
            dictcopy = copy.copy(p._numberToVirtualHelix)
            for i in range(len(self._oldNumbers)):
                oldNum = self._oldNumbers[i]
                newNum = self._newNumbers[i]
                if oldNum != newNum:
                    vh = p._numberToVirtualHelix[newNum]
                    # if the newnumber is already being used 
                    # swap the pair.
                    dictcopy[oldNum] = vh
                    vh.setNumber(oldNum)
                    if newNum not in self._oldNumbers:
                        del dictcopy[newNum]
                    # dnapart owns the idnum of a virtualhelix, so we
                    # are the ones that send an update when it changes
                    # p.virtualHelixAtCoordsChanged.emit(vh.row(), vh.col())
                # end if
            #end for
            p._numberToVirtualHelix = dictcopy
            # self._phg.displayedVHsChanged.emit()
    
    ############################# VirtualHelix ID Number Management #############################
    # Used in both square, honeycomb lattices and so it's shared here
    def reserveHelixIDNumber(self, parityEven=True, requestedIDnum=None):
        """
        Reserves and returns a unique numerical label appropriate for a virtualhelix of
        a given parity. If a specific index is preferable (say, for undo/redo) it can be
        requested in num.
        """
        num = requestedIDnum
        if num != None: # We are handling a request for a particular number
            assert num >= 0, long(num) == num
            assert not num in self._numberToVirtualHelix
            if num in self.oddRecycleBin:
                self.oddRecycleBin.remove(num)
                heapify(self.oddRecycleBin)
                return num
            if num in self.evenRecycleBin:
                self.evenRecycleBin.remove(num)
                heapify(self.evenRecycleBin)
                return num
            self.reserveBin.add(num)
            return num
        # Just find any valid index (subject to parity constraints)
        if parityEven:
             if len(self.evenRecycleBin):
                 return heappop(self.evenRecycleBin)
             else:
                 while self.highestUsedEven + 2 in self.reserveBin:
                     self.highestUsedEven += 2
                 self.highestUsedEven += 2
                 return self.highestUsedEven
        else:
            if len(self.oddRecycleBin):
                return heappop(self.oddRecycleBin)
            else:
                while self.highestUsedOdd + 2 in self.reserveBin:
                    self.highestUsedOdd += 2
                self.highestUsedOdd += 2
                return self.highestUsedOdd

    def recycleHelixIDNumber(self, n):
        """
        The caller's contract is to ensure that n is not used in *any* helix
        at the time of the calling of this function (or afterwards, unless
        reserveLabelForHelix returns the label again)"""
        if n % 2 == 0:
            heappush(self.evenRecycleBin,n)
        else:
            heappush(self.oddRecycleBin,n)

    ################## Transient State (doesn't get saved) ###################
    def selection(self):
        """The set of helices that has been clicked or shift-clicked in
        the slice view. @todo 1) implement this 2) make the selected 
        helices more prominent in the path view (this would allow one 
        to deal with lots and lots of helices)"""
        return list(self._selection)

    selectionWillChange = pyqtSignal(object)
    def setSelection(self, newSelection):
        if self.selectAllBehavior:
            newSelection = self.getVirtualHelices()
        ns = list(newSelection)
        self.selectionWillChange.emit(ns)
        self._selection = ns

    def selectAll(self, *args, **kwargs):
        self.setSelection(self.getVirtualHelices())

    def updateSelectionFromVHChange(self, row, col):
        coord = (row, col)
        vh = self.getVirtualHelix(coord)
        if vh:
            s = self.selection()
            s.append(vh)
            self.setSelection(s)
        else:
            s = [vh for vh in self.selection() if vh.coord()!=coord]
            self.setSelection(s)

    def activeSlice(self):
        """The active slice is the index of the slice selected by the
        vertical slider in the path view"""
        return self._activeSlice

    activeSliceWillChange = pyqtSignal(object)
    def setActiveSlice(self, newSliceIndex):
        ni = clamp(newSliceIndex, 0, self.dimensions()[2]-1)
        if self._activeSlice == ni:
            return
        self.activeSliceWillChange.emit(ni)
        self._activeSlice = ni
    