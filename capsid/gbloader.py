#!/usr/bin/env python
'''Functions for dealing with the records returned from SeqIO and creates lists of dictionary items that can be loaded into MongoDB'''


# Copyright 2011(c) The Ontario Institute for Cancer Reserach. All rights reserved.
#
# This program and the accompanying materials are made available under the
# terms of the GNU Public License v3.0.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.

from itertools import count
from collections import namedtuple

from Bio import SeqIO
import gridfs

import capsid

Record = namedtuple('Record', ['genome', 'features', 'sequence'])
Qualifiers = namedtuple('Qualifiers', ['name', 'geneId', 'locusTag'])
Counter = namedtuple('Counter', ['records', 'genomes', 'features', 'sequences'])

db = None
gfs = None
logger = None
counter = Counter(count(), count(), count(), count())
r_it, g_it, f_it, s_it = count(), count(), count(), count()



def insert_records(record):
    '''Inserts Genome, Features and Sequence into Database'''

    db.genome.save(record.genome)
    [db.feature.save(feature) for feature in record.features]
    if record.sequence:
        gfs.put(record.sequence,_id=record.genome['gi'],chunkSize=80)


def unknown_seq(record):
    '''Filters out unknown sequences that are all 'N' so they are not saved'''

    return 'N' in record.seq


def extract_sequence(record,  genome):
    '''Returns a dictionary of the genome sequence'''
    global counter
    counter.sequences.next()

    if not unknown_seq(record):
        return record.seq.tostring()


def get_qualifiers(qualifiers):
    '''Returns dictionary of useful qualifiers for the feature'''

    locusTag = qualifiers['locus_tag'][0] if 'locus_tag' in qualifiers else None

    try:
        geneId = [int(refs[7:]) for refs in qualifiers["db_xref"] if 'GeneID' in refs][0]
    except (IndexError, KeyError):
        geneId = None

    gene = qualifiers['gene'][0] if 'gene' in qualifiers else None

    name = gene or locusTag or geneId or 'NA'

    return Qualifiers(name, geneId or 'NA', locusTag or 'NA')


def build_subfeatures(feature, genome):
    '''Needed for features with locations that are 'join' or 'order'. Recreates the parent features multiple times using the subfeatures' location.'''

    return [build_feature(feature, genome, sf.location) for sf in feature.sub_features]


def build_feature(feature, genome, sf_location = None):
    '''Returns a dictionary of the feature'''
    global counter
    counter.features.next()

    qualifiers = get_qualifiers(feature.qualifiers)
    feature.location = sf_location or feature.location

    f = {
        "name": qualifiers.name
        , "genome": genome["gi"]
        , "geneId": qualifiers.geneId
        , "locusTag": qualifiers.locusTag
        , "start": feature.location.nofuzzy_start + 1
        , "end": feature.location.nofuzzy_end
        , "operator": feature.location_operator
        , "strand": feature.strand
        , "type": feature.type
        }

    return f


def extract_feature(feature, genome):
    '''Determines the feature's location operator and calls the appropriate build function'''

    has_subs = feature.location_operator in ['join', 'order']

    return build_subfeatures(feature, genome) if has_subs else build_feature(feature, genome)


def extract_features(record, genome):
    '''Returns a list of features belonging to the genome'''

    return (extract_feature(f, genome) for f in record.features[1:] if f.type in ['gene', 'CDS'])


def extract_genome(record):
    '''Returns a dictionary of the genome'''
    global counter
    counter.genomes.next()

    genome = {
        "gi": int(record.annotations['gi'])
        , "name": record.description
        , "accession": record.name
        , "version": record.annotations['sequence_version']
        , "length": record.features[0].location.nofuzzy_end
        , "strand":  record.features[0].strand
        , "taxonomy": record.annotations['taxonomy']
        , "organism": record.annotations['organism']
        }

    return genome


def exists(record, genomes):
    '''Checks if the record's GI exists in the list of GIs from the database'''

    return int(record.annotations['gi']) in genomes


def parse_record(record, dbgenomes):
    '''Pull out genomic information from the GenBank file'''
    global counter
    counter.records.next()

    if exists(record, dbgenomes):
        return None

    genome = extract_genome(record)
    features = extract_features(record, genome)
    sequence = extract_sequence(record, genome)

    return Record(genome, features, sequence)


def get_db_genomes():
    '''Get a list of genomes currently in the db, uses GI to prevent duplication.'''

    return set(g['gi'] for g in db.genome.find({}, {"_id":0, "gi":1}))


def parse_gb_file(f):
    '''Use SeqIO to extract genome data from GenBank files'''
    logger.info('Scanning GenBank File {0}'.format(f))

    with open(f, 'rU') as fh:
        dbgenomes = get_db_genomes()
        records = (parse_record(r, dbgenomes) for r in SeqIO.parse(fh, 'gb'))
        # parse_record will return None if the genome is already in the Database.
        # Using filter(None, records) will put the entire thing in memory,
        # this way it only deals with 1 record at a time and skips the 'None's.
        [insert_records(r) for r in records if r]
        summary()


def summary():
    '''Logging summary of added records'''
    global counter

    # Counter starts at 0, so >it = count(); >print it.next(); >0
    # Using *_it.next here sets it to the correct value for printing.
    records = counter.records.next()
    genomes = counter.genomes.next()
    features = counter.features.next()
    sequences = counter.sequences.next()

    if records:
        logger.info("{0} Genomes found, {1} new Genomes added.".format(records, genomes))
        if features:
            logger.info('{0} Features added successfully!'.format(features))
        if sequences:
            logger.info('{0} Sequences added successfully!'.format(sequences))
    else:
        logger.info('No Genomes found, make sure this is a GenBank file.')

    # Reset the counter for the next file
    counter = Counter(count(), count(), count(), count())


def main(args):
    '''
    Reads through GenBank files and loads data into MongoDB in the genome, feature and sequence collections.
    python gloader.py g1.gbff gb2.gbff',
    '''

    global db, gfs, logger

    logger = args.logging.getLogger(__name__)
    db = capsid.connect(args)
    gfs = gridfs.GridFS(db, 'sequence')

    map(parse_gb_file, args.files)

if __name__ == '__main__':
    print 'This program should be run as part of the capsid package:\n\t$ capsid gbloader -h\n\tor\n\t$ /path/to/capsid/bin/capsid gbloader -h'
